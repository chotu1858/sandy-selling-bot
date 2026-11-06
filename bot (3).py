#!/usr/bin/env python3
"""VC MUSIC v2.1 - single-session voice studio for the hostpanel.

ONE api_id/api_hash, ONE userbot session, ONE voice chat at a time, everything controlled from the bot.
Flow:  send audio/voice to the bot -> pick a mode / mixer -> /join <chat> -> Play  (join + stream = ONE step).

Commands (admins):
  /start /menu        home panel            /admin   admin panel (diagnostics, logs, admins, reload)
  /join <chat>        set target group      /leave   leave VC
  /testtone [chat]    10s sine wave test    /loop    toggle repeat
  /setapi <id> <hash> /setsession <string>  /reload  restart userbot without restarting the bot
  /status /logs       /addadmin <id> /deladmin <id>  (owners = ADMIN_IDS from .env only)
Panel env (.env): BOT_TOKEN ADMIN_IDS STORE_NAME SUPPORT_USERNAME THEME_FILE.  Secrets live in vc_config.json.
"""
import asyncio, collections, html, itertools, json, logging, os, shutil, subprocess, sys, time, wave

CWD = os.getcwd()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for _n in ("httpx", "httpcore", "pyrogram"):      # silence the endless "HTTP 200 OK" lines
    logging.getLogger(_n).setLevel(logging.WARNING)
log = logging.getLogger("vcmusic")


class Ring(logging.Handler):
    """Keeps the last log lines in memory so the admin panel can show them (/logs)."""

    def __init__(self):
        super().__init__()
        self.buf = collections.deque(maxlen=300)

    def emit(self, record):
        try:
            self.buf.append(self.format(record))
        except Exception:
            pass


RING = Ring()
RING.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
logging.getLogger().addHandler(RING)


def load_env(path):
    try:
        for line in open(path, encoding="utf-8").read().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


load_env(os.path.join(CWD, ".env"))
TOKEN = os.getenv("BOT_TOKEN", "")
ADMINS = {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()}   # owners
STORE = os.getenv("STORE_NAME", "VC Music")
SUPPORT = os.getenv("SUPPORT_USERNAME", "@support")
CFG_FILE = os.path.join(CWD, "vc_config.json")
WORK = os.path.join(CWD, "work")
os.makedirs(WORK, exist_ok=True)
PLAY_TIMEOUT = 60      # seconds: C.play() must never hang the bot silently
END_MARGIN = 0.5       # seconds added to the audio length before a loop restarts

try:
    THEME = json.load(open(os.getenv("THEME_FILE", ""), encoding="utf-8"))
except Exception:
    THEME = {}


def T(k, d):
    v = THEME.get(k)
    return v if isinstance(v, str) and v else d


LABELS = {"gain": "🔊 Gain", "loudness": "📢 Loudness", "bass": "🥁 Bass", "treble": "✨ Treble",
          "clarity": "💎 Clarity", "echo": "🌊 Echo", "pitch": "🎼 Pitch"}
LABELS.update(THEME.get("labels") if isinstance(THEME.get("labels"), dict) else {})
DEFAULTS = dict(gain=100, loudness=100, bass=100, treble=100, clarity=100, echo=0, pitch=500)
SETTINGS = dict(DEFAULTS)
MODES = {  # name: (button text, settings)
    "normal": ("🎧 Normal", dict(DEFAULTS)),
    "loud": ("📢 Loud", dict(DEFAULTS, gain=400, loudness=600, bass=300, treble=200, clarity=200)),
    "bass": ("🥁 Bass Boost", dict(DEFAULTS, gain=250, loudness=300, bass=700)),
    "clear": ("💎 Crystal Clear", dict(DEFAULTS, clarity=600, treble=400, gain=150)),
    "echo": ("🌊 Echo Hall", dict(DEFAULTS, echo=500, gain=150)),
    "chip": ("🐿 Chipmunk", dict(DEFAULTS, pitch=800)),
    "deep": ("👹 Deep Voice", dict(DEFAULTS, pitch=200, bass=500)),
    "max": ("🔥 MAX (limited)", dict(DEFAULTS, gain=800, loudness=900, bass=500, treble=300, clarity=300)),
}
STATE = dict(
    chat=None, chat_title="", audio=None, audio_name="", mode="normal",
    playing=False, paused=False, volume=100, loop=False,
    in_call=False,      # userbot is currently inside the VC
    streaming=False,    # a media stream was attached with the join/play
    cur_out=None, cur_dur=0.0, end_at=0.0, remaining=0.0, gen=0, admin_chat=None,
)
_counter = itertools.count(1)
PLAY_LOCK = asyncio.Lock()
_end_task = None


def cfg():
    try:
        return json.load(open(CFG_FILE, encoding="utf-8"))
    except Exception:
        return {}


def save_cfg(c):
    with open(CFG_FILE, "w", encoding="utf-8") as f:
        json.dump(c, f)
    os.chmod(CFG_FILE, 0o600)


def setup_ffmpeg():
    """No root needed: system ffmpeg, or the binary shipped by the imageio-ffmpeg pip package."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        real = imageio_ffmpeg.get_ffmpeg_exe()
        bindir = os.path.join(CWD, "bin")
        os.makedirs(bindir, exist_ok=True)
        link = os.path.join(bindir, "ffmpeg")
        if os.path.lexists(link):
            os.unlink(link)
        os.symlink(real, link)
        os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
        return link
    except Exception as e:
        log.error("ffmpeg not found: %s", e)
        return "ffmpeg"


FFMPEG = setup_ffmpeg()


def ensure_pyrogram():
    """Panel installs wheels only; pyrogram needs pyaes (source-only, pure Python) -> install it here once."""
    try:
        import pyrogram  # noqa
        return
    except ImportError:
        pass
    r = subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check",
                        "pyaes==1.6.1", "pysocks", "pyrogram==2.0.106"], capture_output=True, text=True)
    if r.returncode != 0:
        log.error("pyrogram install failed: %s", (r.stdout + r.stderr)[-1500:])
        raise SystemExit("pyrogram install failed")


ensure_pyrogram()
from telegram import InlineKeyboardButton, InlineKeyboardMarkup as M, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters
from pyrogram import Client
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream, AudioQuality

U = None     # the single userbot client
C = None     # its PyTgCalls
APP = None   # telegram Application (used to push error messages to the admin chat)


# ───────────────────────── helpers ─────────────────────────
def btn(text, cb=None, style="primary", url=None):
    """Coloured button (Bot API 9.4 style: primary blue / success green / danger red)."""
    kw = {"url": url} if url else {"callback_data": cb}
    try:
        return InlineKeyboardButton(text, api_kwargs={"style": style}, **kw)
    except Exception:
        return InlineKeyboardButton(text, **kw)


def admin_ids():
    extra = set()
    for x in cfg().get("admins", []):
        try:
            extra.add(int(x))
        except (TypeError, ValueError):
            pass
    return ADMINS | extra


def is_admin(update):
    u = update.effective_user
    ok = bool(u) and u.id in admin_ids()
    if ok and update.effective_chat:
        STATE["admin_chat"] = update.effective_chat.id
    return ok


def is_owner(update):
    u = update.effective_user
    return bool(u) and u.id in ADMINS


async def fail(chat_id, where, e):
    """Write the full traceback to bot.log AND tell the admin in chat (not only a popup)."""
    log.error("%s failed: %s: %s", where, type(e).__name__, e, exc_info=e)
    chat_id = chat_id or STATE.get("admin_chat")
    if not (chat_id and APP):
        return
    msg = f"❌ <b>{html.escape(where)}</b>\n<code>{html.escape(type(e).__name__)}: {html.escape(str(e))[:600]}</code>"
    try:
        await APP.bot.send_message(chat_id, msg, parse_mode="HTML")
    except Exception:
        log.exception("could not send error message to chat")


def ver(pkg):
    from importlib import metadata
    try:
        return metadata.version(pkg)
    except Exception:
        return "?"


def wav_duration(path):
    try:
        with wave.open(path) as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return 0.0


# ───────────────────────── audio (ffmpeg) ─────────────────────────
def build_filter(s):
    f = ["aresample=48000", f"volume={1 + s['gain'] / 100:.2f}", f"bass=g={s['bass'] / 50:.1f}",
         f"treble=g={s['treble'] / 50:.1f}", f"highpass=f={50 + s['clarity'] / 10:.0f}"]
    if s["loudness"] > 100:
        f.append(f"acompressor=threshold=0.1:ratio={2 + s['loudness'] / 125:.1f}:makeup={1 + s['loudness'] / 250:.1f}")
    if s["echo"]:
        f.append(f"aecho=0.8:0.8:{40 + s['echo'] / 10:.0f}:0.4")
    if s["pitch"] != 500:
        f.append(f"asetrate=48000*{1 + (s['pitch'] - 500) / 2500:.2f},aresample=48000")
    f.append("alimiter=limit=0.9")     # safety cap against clipping / ear damage - keep it
    return ",".join(f)


async def run_ffmpeg(*args):
    p = await asyncio.create_subprocess_exec(FFMPEG, "-y", "-loglevel", "error", *args,
                                             stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await p.communicate()
    if p.returncode != 0:
        raise RuntimeError("ffmpeg: " + err.decode(errors="replace")[-300:])


async def process_audio():
    out = os.path.join(WORK, f"out_{next(_counter)}.wav")
    await run_ffmpeg("-i", STATE["audio"], "-vn", "-af", build_filter(SETTINGS), "-ar", "48000", "-ac", "2", out)
    for old in os.listdir(WORK):          # keep the work folder small
        if old.startswith("out_") and os.path.join(WORK, old) != out:
            try:
                os.remove(os.path.join(WORK, old))
            except OSError:
                pass
    return out


async def make_tone():
    out = os.path.join(WORK, "tone.wav")
    await run_ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=10", "-af", "volume=0.5",
                     "-ar", "48000", "-ac", "2", out)
    return out


# ───────────────────────── VC engine (py-tgcalls 2.x) ─────────────────────────
def make_stream(path):
    return MediaStream(path, AudioQuality.HIGH, video_flags=MediaStream.Flags.IGNORE)


def cancel_timer():
    global _end_task
    t, _end_task = _end_task, None
    if t and not t.done():
        t.cancel()


def schedule_end(seconds):
    global _end_task
    cancel_timer()
    if seconds > 0:
        STATE["end_at"] = time.monotonic() + seconds
        _end_task = asyncio.create_task(_on_end(seconds, STATE["gen"]))


async def _on_end(seconds, gen):
    """Fires when the audio should be finished: restarts it if loop is on, otherwise marks idle."""
    global _end_task
    try:
        await asyncio.sleep(seconds + END_MARGIN)
    except asyncio.CancelledError:
        return
    _end_task = None            # so start_playing()->schedule_end() does not cancel THIS task
    if gen != STATE["gen"] or STATE["paused"]:
        return
    if STATE["loop"] and STATE["cur_out"] and STATE["chat"]:
        try:
            async with PLAY_LOCK:
                if gen != STATE["gen"]:
                    return
                await start_playing(STATE["cur_out"])
            log.info("loop: restarted %s", os.path.basename(STATE["cur_out"]))
        except Exception as e:
            STATE.update(playing=False, streaming=False)
            await fail(None, "Loop restart", e)
    else:
        STATE.update(playing=False, streaming=False)
        log.info("playback finished")


async def safe_leave(chat):
    cancel_timer()
    try:
        await asyncio.wait_for(C.leave_call(chat), 20)
    except Exception as e:
        log.warning("leave_call(%s): %s: %s", chat, type(e).__name__, e)
    STATE.update(in_call=False, streaming=False, playing=False, paused=False)


async def vc_start(path):
    """Join (if needed) AND attach the stream in ONE C.play() call, so the account is never joined-but-muted."""
    chat = STATE["chat"]
    if STATE["in_call"] and not STATE["streaming"]:
        # earlier silent join / finished stream: the mic stays muted -> leave and re-join WITH the stream
        log.info("in call without stream -> fresh join with stream")
        await safe_leave(chat)
        await asyncio.sleep(1)
    try:
        await asyncio.wait_for(C.play(chat, make_stream(path)), PLAY_TIMEOUT)
    except Exception as e:
        if not STATE["in_call"]:
            raise
        log.error("C.play on a live call failed (%s: %s) -> retry with a fresh join", type(e).__name__, e, exc_info=e)
        await safe_leave(chat)
        await asyncio.sleep(1)
        await asyncio.wait_for(C.play(chat, make_stream(path)), PLAY_TIMEOUT)
    STATE.update(in_call=True, streaming=True, playing=True, paused=False)
    try:
        await C.change_volume_call(chat, STATE["volume"])
    except Exception:
        log.exception("change_volume_call failed (stream is still playing)")


async def start_playing(path):
    """Caller must hold PLAY_LOCK."""
    cancel_timer()
    STATE["gen"] += 1
    dur = wav_duration(path)
    STATE.update(cur_out=path, cur_dur=dur)
    log.info("play: chat=%s file=%s dur=%.1fs size=%s", STATE["chat"], os.path.basename(path), dur,
             os.path.getsize(path))
    await vc_start(path)
    schedule_end(dur)


async def do_play():
    async with PLAY_LOCK:
        out = await process_audio()
        await start_playing(out)


async def do_tone():
    async with PLAY_LOCK:
        out = await make_tone()
        await start_playing(out)


async def resolve_chat(arg):
    key = int(arg) if arg.lstrip("-").isdigit() else arg
    try:
        return await U.get_chat(key)
    except Exception:
        if isinstance(key, int):           # string sessions start with an empty peer cache -> refresh and retry
            await warm_peers()
            return await U.get_chat(key)
        raise


async def set_target(chat):
    if STATE["chat"] and STATE["chat"] != chat.id and STATE["in_call"]:      # one VC at a time
        await safe_leave(STATE["chat"])
    STATE.update(chat=chat.id, chat_title=chat.title or str(chat.id))


async def warm_peers():
    n = 0
    try:
        async for _ in U.get_dialogs(limit=500):
            n += 1
    except Exception:
        log.exception("get_dialogs failed")
    log.info("peer cache warmed: %s dialogs", n)


# ───────────────────────── UI ─────────────────────────
def bar(v):
    n = round(v / 100)
    return T("bar_full", "█") * n + T("bar_empty", "░") * (10 - n)


def status_text():
    vc = html.escape(STATE["chat_title"] or "—")
    au = html.escape(STATE["audio_name"] or "— (send me an audio / voice)")
    mode = MODES[STATE["mode"]][0] if STATE["mode"] in MODES else "🎛 Custom"
    st = "▶️ playing" if STATE["playing"] and not STATE["paused"] else ("⏸ paused" if STATE["paused"] else "⏹ idle")
    return (f"🎙 <b>VC:</b> {vc}{' 🟢 in call' if STATE['in_call'] else ''}\n🎵 <b>Audio:</b> {au}\n🎚 <b>Mode:</b> {mode}\n"
            f"🔊 <b>Volume:</b> {STATE['volume']}%\n🔁 <b>Loop:</b> {'ON' if STATE['loop'] else 'off'}\n📡 <b>State:</b> {st}")


def home_kb():
    return M([
        [btn(T("btn_join", "🎙 Join VC"), "join", "success"), btn(T("btn_leave", "🚪 Leave"), "leave", "danger")],
        [btn(T("btn_play", "▶️ Play"), "play", "success"), btn(T("btn_pause", "⏸ Pause"), "pause", "primary"),
         btn(T("btn_resume", "⏯ Resume"), "resume", "primary")],
        [btn(T("btn_modes", "🎚 Modes"), "modes", "primary"), btn(T("btn_mixer", "🎛 Mixer"), "mix", "primary")],
        [btn("🔁 Loop: ON" if STATE["loop"] else "🔁 Loop: off", "loop", "success" if STATE["loop"] else "primary"),
         btn("🧪 Test tone", "tone", "primary"), btn("🛠 Admin", "adm:home", "primary")],
        [btn("🔉 50%", "vol:50", "primary"), btn("🔊 100%", "vol:100", "success"),
         btn("📢 150%", "vol:150", "primary"), btn("🔥 200%", "vol:200", "danger")],
        [btn(T("btn_support", "💬 Support"), url=f"https://t.me/{SUPPORT.lstrip('@')}")],
    ])


def modes_kb():
    items = list(MODES.items())
    rows = []
    for i in range(0, len(items), 2):
        rows.append([btn(("✅ " if STATE["mode"] == k else "") + v[0], f"mode:{k}", "success" if STATE["mode"] == k else "primary")
                     for k, v in items[i:i + 2]])
    rows.append([btn("⬅️ Back", "home", "danger")])
    return M(rows)


def mix_text():
    rows = "\n".join(f"{LABELS[k]}: <code>{bar(v)}</code> {v}/1000" for k, v in SETTINGS.items())
    return f"{T('panel_title', '🎛 <b>CUSTOM MIX</b>')}\n\n{rows}"


def mix_kb():
    rows = []
    for k, v in SETTINGS.items():
        rows.append([btn("➖", f"s:{k}:-100", "danger"), btn(f"{LABELS[k]} {v}", "noop", "primary"),
                     btn("➕", f"s:{k}:100", "success")])
    rows.append([btn(T("btn_reset", "🔄 Reset"), "reset", "danger"), btn(T("btn_play", "▶️ Apply & Play"), "play", "success")])
    rows.append([btn("⬅️ Back", "home", "primary")])
    return M(rows)


def admin_text():
    c = cfg()
    ok = lambda x: "✅" if x else "❌"
    extra = sorted(admin_ids() - ADMINS)
    return ("🛠 <b>Admin panel</b>\n\n"
            f"API {ok(c.get('api_id') and c.get('api_hash'))}   Session {ok(c.get('session'))}   Userbot {ok(C)}\n"
            f"In call {ok(STATE['in_call'])}   Streaming {ok(STATE['streaming'])}   Loop {ok(STATE['loop'])}\n"
            f"👑 Owners: <code>{', '.join(map(str, sorted(ADMINS))) or '—'}</code>\n"
            f"👥 Extra admins: <code>{', '.join(map(str, extra)) or '—'}</code>\n\n" + status_text())


def admin_kb():
    return M([
        [btn("🩺 Diagnose", "adm:diag", "primary"), btn("📜 Logs", "adm:logs", "primary")],
        [btn("🧪 Test tone", "tone", "success"), btn("🔁 Loop: ON" if STATE["loop"] else "🔁 Loop: off", "loop:adm",
                                                    "success" if STATE["loop"] else "primary")],
        [btn("🔄 Reload userbot", "adm:reload", "danger"), btn("🧹 Clear work", "adm:clear", "primary")],
        [btn("👥 How to add admins", "adm:admins", "primary")],
        [btn("🏠 Home", "home", "primary")],
    ])


async def diagnose_text():
    try:
        p = await asyncio.create_subprocess_exec(FFMPEG, "-version", stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.DEVNULL)
        out, _ = await p.communicate()
        ff = out.decode(errors="replace").splitlines()[0][:60]
    except Exception as e:
        ff = f"ERROR {e}"
    files = []
    for n in sorted(os.listdir(WORK)):
        try:
            files.append(f"{n} {os.path.getsize(os.path.join(WORK, n)) // 1024}KB")
        except OSError:
            pass
    return ("🩺 <b>Diagnostics</b>\n"
            f"python <code>{sys.version.split()[0]}</code>\n"
            f"py-tgcalls <code>{ver('py-tgcalls')}</code>  ntgcalls <code>{ver('ntgcalls')}</code>\n"
            f"pyrogram <code>{ver('pyrogram')}</code>  PTB <code>{ver('python-telegram-bot')}</code>\n"
            f"ffmpeg <code>{html.escape(FFMPEG)}</code>\n<code>{html.escape(ff)}</code>\n"
            f"chat <code>{STATE['chat']}</code>  in_call <code>{STATE['in_call']}</code>  streaming <code>{STATE['streaming']}</code>\n"
            f"current file <code>{html.escape(os.path.basename(STATE['cur_out'] or '-'))}</code> ({STATE['cur_dur']:.1f}s)\n"
            f"work: <code>{html.escape(', '.join(files) or 'empty')}</code>")


def logs_text(n=25):
    lines = list(RING.buf)[-n:]
    body = html.escape("\n".join(lines))[-3500:] or "(no log lines yet)"
    return f"📜 <b>Last {len(lines)} log lines</b>\n<pre>{body}</pre>"


# ───────────────────────── commands ─────────────────────────
async def start(update: Update, ctx):
    text = T("welcome", "🎙 <b>{store}</b>").replace("{store}", html.escape(STORE))
    if is_admin(update):
        await update.message.reply_text(f"{text}\n\n{status_text()}", parse_mode="HTML", reply_markup=home_kb())
    else:
        await update.message.reply_text(text, parse_mode="HTML",
                                        reply_markup=M([[btn(T("btn_support", "💬 Support"), url=f"https://t.me/{SUPPORT.lstrip('@')}")]]))


async def menu(update: Update, ctx):
    if is_admin(update):
        await update.message.reply_text(status_text(), parse_mode="HTML", reply_markup=home_kb())


async def admin_cmd(update: Update, ctx):
    if is_admin(update):
        await update.message.reply_text(admin_text(), parse_mode="HTML", reply_markup=admin_kb())


async def setapi(update: Update, ctx):
    if not is_admin(update):
        return
    try:
        i, h = int(ctx.args[0]), ctx.args[1]
    except Exception:
        return await update.message.reply_text("Use: /setapi <api_id> <api_hash>")
    c = cfg(); c.update(api_id=i, api_hash=h); save_cfg(c)
    try:
        await update.message.delete()
    except Exception:
        pass
    await update.effective_chat.send_message("✅ API saved. Now /reload (or restart the bot).")


async def setsession(update: Update, ctx):
    if not is_admin(update):
        return
    try:
        s = ctx.args[0]
    except Exception:
        return await update.message.reply_text("Use: /setsession <string_session>")
    c = cfg(); c["session"] = s; save_cfg(c)
    try:
        await update.message.delete()
    except Exception:
        pass
    await update.effective_chat.send_message("✅ Session saved. Send /reload to start the userbot (no panel restart needed).")


async def status(update: Update, ctx):
    if not is_admin(update):
        return
    await update.message.reply_text(admin_text(), parse_mode="HTML")


async def logs_cmd(update: Update, ctx):
    if is_admin(update):
        await update.message.reply_text(logs_text(), parse_mode="HTML")


async def reload_cmd(update: Update, ctx):
    if not is_admin(update):
        return
    m = await update.message.reply_text("🔄 Restarting userbot…")
    ok = await start_userbot()
    await m.edit_text("✅ Userbot ready" if ok else "❌ Userbot did not start - check /logs")


async def join_cmd(update: Update, ctx):
    """Sets the target group. The actual VC join happens together with the stream on Play / /testtone."""
    if not is_admin(update):
        return
    if not C:
        return await update.message.reply_text("Userbot not running. /setapi + /setsession, then /reload.")
    if not ctx.args:
        return await update.message.reply_text("Use: /join <group id or @username>\nThe session account must already be a member of that group.")
    try:
        chat = await resolve_chat(ctx.args[0])
        await set_target(chat)
    except Exception as e:
        return await fail(update.effective_chat.id, "/join", e)
    await update.message.reply_text(
        "✅ Target set. The account joins the VC the moment you press ▶️ Play (join + audio in one step, so the mic is never muted).\n\n"
        + status_text(), parse_mode="HTML", reply_markup=home_kb())


async def leave_cmd(update: Update, ctx):
    if not is_admin(update):
        return
    if C and STATE["chat"]:
        await safe_leave(STATE["chat"])
    await update.message.reply_text("🚪 Left VC\n\n" + status_text(), parse_mode="HTML", reply_markup=home_kb())


async def loop_cmd(update: Update, ctx):
    if is_admin(update):
        STATE["loop"] = not STATE["loop"]
        await update.message.reply_text(f"🔁 Loop {'ON' if STATE['loop'] else 'off'}", reply_markup=home_kb())


async def testtone_cmd(update: Update, ctx):
    if not is_admin(update):
        return
    if not C:
        return await update.message.reply_text("Userbot not running. /setapi + /setsession, then /reload.")
    try:
        if ctx.args:
            await set_target(await resolve_chat(ctx.args[0]))
        if not STATE["chat"]:
            return await update.message.reply_text("Use: /testtone <group id or @username>  (or /join first)")
        m = await update.message.reply_text("🧪 Joining VC and playing a 10s 440 Hz tone…")
        await do_tone()
        await m.edit_text("🧪 Tone started - you should hear a beep for 10s.\n\n" + status_text(), parse_mode="HTML",
                          reply_markup=home_kb())
    except Exception as e:
        await fail(update.effective_chat.id, "/testtone", e)


async def addadmin(update: Update, ctx):
    if not is_owner(update):
        return
    try:
        uid = int(ctx.args[0])
    except Exception:
        return await update.message.reply_text("Use: /addadmin <user_id>")
    c = cfg(); a = {int(x) for x in c.get("admins", [])}; a.add(uid); c["admins"] = sorted(a); save_cfg(c)
    await update.message.reply_text(f"✅ {uid} is now an admin.")


async def deladmin(update: Update, ctx):
    if not is_owner(update):
        return
    try:
        uid = int(ctx.args[0])
    except Exception:
        return await update.message.reply_text("Use: /deladmin <user_id>")
    c = cfg(); c["admins"] = sorted({int(x) for x in c.get("admins", [])} - {uid}); save_cfg(c)
    await update.message.reply_text(f"✅ {uid} removed.")


async def got_audio(update: Update, ctx):
    if not is_admin(update):
        return
    m = update.message
    att = m.voice or m.audio or m.document
    if not att:
        return
    try:
        f = await att.get_file()
        ext = os.path.splitext(f.file_path or "")[1] or ".ogg"
        path = os.path.join(WORK, "input" + ext)
        await f.download_to_drive(path)
    except Exception as e:
        return await fail(update.effective_chat.id, "Audio download (max 20 MB)", e)
    STATE["audio"] = path
    STATE["audio_name"] = getattr(att, "file_name", None) or ("voice message" if m.voice else "audio")
    await m.reply_text("✅ Audio received. Pick a mode, /join a group, then Play.\n\n" + status_text(),
                       parse_mode="HTML", reply_markup=home_kb())


# ───────────────────────── callbacks ─────────────────────────
async def cb(update: Update, ctx):
    q = update.callback_query
    if not is_admin(update):
        return await q.answer("Admins only", show_alert=True)
    d = q.data
    chat_id = update.effective_chat.id if update.effective_chat else None

    async def show(text, kb):
        try:
            await q.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            if "not modified" not in str(e).lower():
                log.warning("edit_text failed: %s", e)

    try:
        if d == "noop":
            return await q.answer()
        if d == "home":
            await show(status_text(), home_kb()); return await q.answer()
        if d == "modes":
            await show("🎚 <b>Pick a mode</b>\n\n" + status_text(), modes_kb()); return await q.answer()
        if d == "mix":
            await show(mix_text(), mix_kb()); return await q.answer()
        if d == "join":
            return await q.answer("Send: /join <group id or @username>  - then press Play", show_alert=True)
        if d.startswith("loop"):
            STATE["loop"] = not STATE["loop"]
            await show(admin_text() if d == "loop:adm" else status_text(), admin_kb() if d == "loop:adm" else home_kb())
            return await q.answer("Loop ON" if STATE["loop"] else "Loop off")
        if d.startswith("adm:"):
            if not is_admin(update):
                return await q.answer("Admins only", show_alert=True)
            k = d[4:]
            if k == "home":
                await show(admin_text(), admin_kb()); return await q.answer()
            if k == "diag":
                await q.answer(); await show(await diagnose_text(), M([[btn("⬅️ Back", "adm:home", "primary")]])); return
            if k == "logs":
                await q.answer(); await show(logs_text(), M([[btn("🔃 Refresh", "adm:logs", "success"), btn("⬅️ Back", "adm:home", "primary")]])); return
            if k == "reload":
                await q.answer("Restarting userbot…")
                ok = await start_userbot()
                await show(admin_text(), admin_kb())
                if not ok:
                    await fail(chat_id, "Reload userbot", RuntimeError("did not start - see /logs"))
                return
            if k == "clear":
                n = 0
                for f in os.listdir(WORK):
                    if f.startswith("out_") or f == "tone.wav":
                        try:
                            os.remove(os.path.join(WORK, f)); n += 1
                        except OSError:
                            pass
                return await q.answer(f"Removed {n} temp files", show_alert=True)
            if k == "admins":
                return await q.answer("Owners (ADMIN_IDS in panel) can use /addadmin <id> and /deladmin <id>.", show_alert=True)
        if d.startswith("mode:"):
            k = d.split(":")[1]
            if k in MODES:
                SETTINGS.clear(); SETTINGS.update(MODES[k][1]); STATE["mode"] = k
            await show("🎚 <b>Pick a mode</b>\n\n" + status_text(), modes_kb())
            if STATE["playing"] and STATE["audio"] and STATE["chat"] and C:
                await q.answer("Applying…")
                await do_play()
                return
            return await q.answer("Mode set")
        if d == "reset":
            SETTINGS.clear(); SETTINGS.update(DEFAULTS); STATE["mode"] = "normal"
            await show(mix_text(), mix_kb()); return await q.answer("Reset")
        if d.startswith("s:"):
            _, k, step = d.split(":")
            if k in SETTINGS:
                SETTINGS[k] = max(0, min(1000, SETTINGS[k] + int(step)))
                STATE["mode"] = "custom"
            await show(mix_text(), mix_kb())
            return await q.answer()          # sliders apply when you press Play (no re-encode on every tap)
        if d.startswith("vol:"):
            STATE["volume"] = int(d.split(":")[1])
            if STATE["chat"] and C and STATE["in_call"]:
                await C.change_volume_call(STATE["chat"], STATE["volume"])
            await show(status_text(), home_kb()); return await q.answer(f"Volume {STATE['volume']}%")

        # actions that need the VC
        if not C or not STATE["chat"]:
            return await q.answer("First: /join <group id or @username>", show_alert=True)
        if d == "tone":
            await q.answer("Joining + playing tone…")
            await do_tone()
            await show(status_text(), home_kb())
        elif d == "play":
            if not STATE["audio"]:
                return await q.answer("Send me an audio / voice first", show_alert=True)
            await q.answer("Processing…")
            await do_play()
            await show(status_text(), home_kb())
        elif d == "pause":
            await C.pause(STATE["chat"]); STATE["paused"] = True
            STATE["remaining"] = max(0.0, STATE["end_at"] - time.monotonic())
            cancel_timer()
            await show(status_text(), home_kb()); await q.answer("Paused")
        elif d == "resume":
            await C.resume(STATE["chat"]); STATE["paused"] = False
            schedule_end(STATE["remaining"])
            await show(status_text(), home_kb()); await q.answer("Resumed")
        elif d == "leave":
            await safe_leave(STATE["chat"])
            await show(status_text(), home_kb()); await q.answer("Left VC")
    except Exception as e:
        try:
            await q.answer(f"{type(e).__name__}: {str(e)[:150]}", show_alert=True)
        except Exception:
            pass
        await fail(chat_id, f"Button '{d}'", e)


async def on_error(update, ctx):
    log.error("Unhandled error in handler", exc_info=ctx.error)


# ───────────────────────── userbot lifecycle ─────────────────────────
async def stop_userbot():
    global U, C
    cancel_timer()
    if C and STATE["chat"] and STATE["in_call"]:
        try:
            await asyncio.wait_for(C.leave_call(STATE["chat"]), 15)
        except Exception:
            pass
    if U:
        try:
            await U.stop()
        except Exception:
            pass
    U = C = None
    STATE.update(in_call=False, streaming=False, playing=False, paused=False)


async def start_userbot():
    global U, C
    await stop_userbot()
    c = cfg()
    if not (c.get("api_id") and c.get("api_hash") and c.get("session")):
        log.info("Waiting for /setapi and /setsession from the admin")
        return False
    try:
        U = Client("vcuser", api_id=int(c["api_id"]), api_hash=c["api_hash"], session_string=c["session"], workdir=CWD)
        await U.start()
        await warm_peers()
        C = PyTgCalls(U)
        await C.start()
        log.info("Userbot ready (py-tgcalls %s, ntgcalls %s, pyrogram %s)", ver("py-tgcalls"), ver("ntgcalls"), ver("pyrogram"))
        return True
    except Exception as e:
        log.error("Userbot start failed: %s: %s", type(e).__name__, e, exc_info=e)
        U = C = None
        return False


async def post_init(app):
    global APP
    APP = app
    await start_userbot()


async def post_shutdown(app):
    await stop_userbot()


def main():
    if not TOKEN:
        raise SystemExit("BOT_TOKEN missing")
    app = (Application.builder().token(TOKEN).concurrent_updates(True)   # a stuck VC call must not freeze the buttons
           .post_init(post_init).post_shutdown(post_shutdown).build())
    for name, fn in (("start", start), ("menu", menu), ("panel", menu), ("admin", admin_cmd), ("setapi", setapi),
                     ("setsession", setsession), ("status", status), ("join", join_cmd), ("leave", leave_cmd),
                     ("testtone", testtone_cmd), ("loop", loop_cmd), ("reload", reload_cmd), ("logs", logs_cmd),
                     ("addadmin", addadmin), ("deladmin", deladmin)):
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(MessageHandler((filters.VOICE | filters.AUDIO | filters.Document.AUDIO) & filters.ChatType.PRIVATE, got_audio))
    app.add_handler(CallbackQueryHandler(cb))
    app.add_error_handler(on_error)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
