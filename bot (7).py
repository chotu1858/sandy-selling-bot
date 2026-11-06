#!/usr/bin/env python3
"""VC MUSIC v2.5 - single-session voice studio for the hostpanel.

ONE api_id/api_hash, ONE userbot session, ONE voice chat at a time, everything controlled from the bot.
Flow:  send audio/voice to the bot -> pick a mode / mixer -> /join <chat> -> Play  (join + stream = ONE step).

Commands (admins):
  /start /menu        home panel            /admin   admin panel (diagnostics, logs, admins, reload)
  /join <chat>        set target group      /leave   leave VC
  /testtone [chat]    10s sine wave test    /loop    toggle repeat
  /setapi <id> <hash> /setsession <string>  /reload  restart userbot without restarting the bot
  /status /logs       /addadmin <id> /deladmin <id>  (owners = ADMIN_IDS from .env only)
  /setwelcome <text>  /setlogo (send a photo)  /cancel   -> or use Admin panel -> Welcome setup
  /sessionhelp        how to make a valid Pyrogram 2.x string session
  /login (phone) /setsession (string) /logout /say <text> /help  -> or Admin panel -> Account login / Commands
Panel env (.env): BOT_TOKEN ADMIN_IDS STORE_NAME SUPPORT_USERNAME THEME_FILE.  Secrets live in vc_config.json.
"""
import asyncio, base64, collections, html, itertools, json, logging, os, re, shutil, subprocess, sys, time, wave

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


_SC = dict(zip("abcdefghijklmnopqrstuvwxyz", "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ"))
_SC.update({k.upper(): v for k, v in list(_SC.items())})
_SC_TABLE = str.maketrans(_SC)


def sc(text):
    """ᴀ ʙ ᴄ ᴅ small-caps font. Only plain A-Z letters change; emojis, digits and other fonts stay as they are."""
    return str(text).translate(_SC_TABLE)


DEFAULT_WELCOME = ("✦ <b>{store}</b> ✦\n━━━━━━━━━━━━━━━━━━\n"
                   f"🎙 <b>{sc('Live VC Studio')}</b>\n"
                   f"┣ 🎧 {sc('Pro mixer + sound modes')}\n"
                   f"┣ 🔊 {sc('Crystal clear audio')}\n"
                   f"┗ 🔁 {sc('Loop • volume control')}\n"
                   "━━━━━━━━━━━━━━━━━━")
PENDING = {}   # admin user id -> "text" | "logo" | "session" | "api" | "tts": what the next message should set
LOGIN = {}     # admin user id -> phone-login state {step, client, phone, hash, ts}
LOGIN_TTL = 600

COMMANDS = [   # (command, usage, description)  - shown in /help, Admin panel and the Telegram '/' menu
    ("start", "/start", "Home panel"),
    ("help", "/help", "All commands + support"),
    ("admin", "/admin", "Admin panel"),
    ("join", "/join <group id or @username>", "Set the target group (VC joins on Play)"),
    ("leave", "/leave", "Leave the voice chat"),
    ("testtone", "/testtone [group]", "10 second beep to test the VC"),
    ("say", "/say [hi|en] <text>", "Speak a text in Indian voice (Hindi / Indian English)"),
    ("loop", "/loop", "Repeat on / off"),
    ("login", "/login", "Login with YOUR phone number (code + 2-step password)"),
    ("setsession", "/setsession <string>", "Or paste a Pyrogram 2.x string session"),
    ("setapi", "/setapi <api_id> <api_hash>", "API ID / HASH from my.telegram.org"),
    ("sessionhelp", "/sessionhelp", "How to make a string session"),
    ("logout", "/logout", "Remove the saved account"),
    ("reload", "/reload", "Restart the userbot"),
    ("status", "/status", "Status"),
    ("logs", "/logs", "Last log lines"),
    ("setwelcome", "/setwelcome <text>", "Welcome text"),
    ("setlogo", "/setlogo", "Welcome logo (send a photo)"),
    ("cancel", "/cancel", "Cancel the current step"),
    ("addadmin", "/addadmin <id>", "Add an admin (owner only)"),
    ("deladmin", "/deladmin <id>", "Remove an admin (owner only)"),
]

LABELS = {"gain": "🔊 Gain", "loudness": "📢 Loudness", "bass": "🥁 Bass", "treble": "✨ Treble",
          "clarity": "💎 Clarity", "echo": "🌊 Echo", "pitch": "🎼 Pitch"}
LABELS.update(THEME.get("labels") if isinstance(THEME.get("labels"), dict) else {})
DEFAULTS = dict(gain=100, loudness=100, bass=100, treble=100, clarity=100, echo=0, pitch=500)
SETTINGS = dict(DEFAULTS)
MODES = {  # sound modes: name -> (button text, settings)
    "normal": ("🎧 Normal", dict(DEFAULTS)),
    "loud": ("📢 Loud", dict(DEFAULTS, gain=400, loudness=600, bass=300, treble=200, clarity=200)),
    "bass": ("🥁 Bass Boost", dict(DEFAULTS, gain=250, loudness=300, bass=700)),
    "clear": ("💎 Crystal Clear", dict(DEFAULTS, clarity=600, treble=400, gain=150)),
    "echo": ("🌊 Echo Hall", dict(DEFAULTS, echo=500, gain=150)),
    "max": ("🔥 MAX (limited)", dict(DEFAULTS, gain=800, loudness=900, bass=500, treble=300, clarity=300)),
}
VOICE = {  # voice filters (they change the voice of the audio you send): name -> (button text, settings)
    "girl": ("👩 Girl voice", dict(DEFAULTS, pitch=880, treble=200, clarity=250)),
    "man": ("👨 Man voice", dict(DEFAULTS, pitch=130, bass=250)),
    "kid": ("🧒 Kid voice", dict(DEFAULTS, pitch=1000, treble=200)),
    "chip": ("🐿 Chipmunk", dict(DEFAULTS, pitch=800)),
    "deep": ("🐻 Deep voice", dict(DEFAULTS, pitch=200, bass=500)),
    "monster": ("👹 Monster", dict(DEFAULTS, pitch=0, bass=600, echo=250)),
    "robot": ("🤖 Robot", dict(DEFAULTS)),
    "alien": ("👽 Alien", dict(DEFAULTS, pitch=700)),
    "ghost": ("👻 Ghost", dict(DEFAULTS, pitch=350)),
    "filmy": ("🎬 Filmy villain", dict(DEFAULTS, pitch=150, bass=400)),
    "rj": ("🎙 RJ radio voice", dict(DEFAULTS, gain=200, loudness=500, clarity=400, treble=300)),
    "whisper": ("🤫 Whisper", dict(DEFAULTS, gain=300)),
    "radio": ("📻 Radio / phone", dict(DEFAULTS)),
    "mega": ("📣 Megaphone", dict(DEFAULTS)),
    "cave": ("🕳 Cave", dict(DEFAULTS)),
    "water": ("🫧 Underwater", dict(DEFAULTS)),
}
ALL_MODES = {**MODES, **VOICE}
FX = {  # extra ffmpeg filter chain per mode (added after the mixer settings, before the safety limiter)
    "robot": "afftfilt=real='hypot(re,im)*sin(0)':imag='hypot(re,im)*cos(0)':win_size=512:overlap=0.75",
    "whisper": "afftfilt=real='hypot(re,im)*cos((random(0)*2-1)*2*3.14)':imag='hypot(re,im)*sin((random(0)*2-1)*2*3.14)':win_size=128:overlap=0.8",
    "radio": "highpass=f=450,lowpass=f=3000,acompressor=threshold=0.08:ratio=6:makeup=2",
    "mega": "highpass=f=600,lowpass=f=3800,acrusher=bits=9:mix=0.35:mode=log:aa=1,acompressor=threshold=0.05:ratio=10:makeup=3",
    "cave": "aecho=0.8:0.88:700|1200|1900:0.4|0.3|0.2",
    "filmy": "aecho=0.8:0.88:300|600:0.4|0.3",
    "water": "lowpass=f=900,aecho=0.8:0.85:80:0.5,vibrato=f=4:d=0.3",
    "alien": "vibrato=f=9:d=0.7,aecho=0.8:0.7:20:0.5",
    "ghost": "vibrato=f=5:d=0.4,aecho=0.8:0.9:500|900:0.4|0.3",
}
STATE = dict(
    chat=None, chat_title="", audio=None, audio_name="", mode="normal",
    playing=False, paused=False, volume=100, loop=False, ub_error="", me="", tts_lang="hi",
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
FFPROBE = shutil.which("ffprobe")


def ensure_ffprobe():
    """py-tgcalls runs `ffprobe` on every media file, but imageio-ffmpeg ships ffmpeg only.
    No root needed: download a static ffprobe once into <bot folder>/bin (needs internet, ~30 MB)."""
    global FFPROBE
    bindir = os.path.join(CWD, "bin")
    dest = os.path.join(bindir, "ffprobe")
    os.makedirs(bindir, exist_ok=True)
    if bindir not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = bindir + os.pathsep + os.environ.get("PATH", "")
    if shutil.which("ffprobe"):
        FFPROBE = shutil.which("ffprobe")
        return FFPROBE
    import platform, tarfile, tempfile, urllib.request
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine().lower())
    if not arch:
        log.error("ffprobe missing and no static build for CPU %s", platform.machine())
        return None
    url = f"https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-{arch}-static.tar.xz"
    tmp = None
    try:
        log.info("ffprobe missing -> downloading static build (one time, ~30 MB): %s", url)
        with tempfile.NamedTemporaryFile(dir=bindir, suffix=".tar.xz", delete=False) as t:
            tmp = t.name
            with urllib.request.urlopen(url, timeout=180) as r:
                shutil.copyfileobj(r, t)
        with tarfile.open(tmp, "r:xz") as tf:
            for m in tf:
                if m.isfile() and os.path.basename(m.name) == "ffprobe":
                    with tf.extractfile(m) as src, open(dest + ".part", "wb") as out:
                        shutil.copyfileobj(src, out)
                    os.replace(dest + ".part", dest)
                    os.chmod(dest, 0o755)
                    break
        FFPROBE = shutil.which("ffprobe")
        log.info("ffprobe ready: %s", FFPROBE)
        return FFPROBE
    except Exception as e:
        log.error("could not install ffprobe: %s: %s", type(e).__name__, e, exc_info=e)
        return None
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


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
from telegram import BotCommand, BotCommandScopeChat, InlineKeyboardButton, InlineKeyboardMarkup as M, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters
from pyrogram import Client, errors as perr
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream, AudioQuality

U = None     # the single userbot client
C = None     # its PyTgCalls
APP = None   # telegram Application (used to push error messages to the admin chat)


# ───────────────────────── helpers ─────────────────────────
def btn(text, cb=None, style="primary", url=None):
    """Coloured button (Bot API 9.4 style: primary blue / success green / danger red)."""
    kw = {"url": url} if url else {"callback_data": cb}
    text = sc(text)
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


def analyse_session(raw):
    """Pyrogram 2.x string session = urlsafe-base64 of exactly 271 bytes. -> (ok, cleaned_string, problem_text)"""
    s = re.sub(r"\s+", "", str(raw or "")).strip("'\"`")

    def dec(x):
        try:
            return base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))
        except Exception:
            return None

    b = dec(s)
    if b is not None and len(b) == 271:
        return True, s, ""
    t = dec(s[1:]) if s[:1] == "1" else None
    if t is not None and len(t) in (263, 275):
        why = "ye TELETHON ki string session hai (1 se shuru hoti hai) - Pyrogram ko nahi chalegi"
    elif b is not None and len(b) == 267:
        why = "ye purane Pyrogram v1 format ki string hai"
    elif b is None:
        why = "string adhoori/kharab hai (copy mein kuch cut ya badal gaya)"
    else:
        why = f"string {len(b)} bytes ki nikli, Pyrogram 2.x ko 271 chahiye (lagbhag 362 characters)"
    return False, s, why


def wav_duration(path):
    try:
        with wave.open(path) as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:
        return 0.0


# ───────────────────────── audio (ffmpeg) ─────────────────────────
def build_filter(s, fx=""):
    f = ["aresample=48000", f"volume={1 + s['gain'] / 100:.2f}", f"bass=g={s['bass'] / 50:.1f}",
         f"treble=g={s['treble'] / 50:.1f}", f"highpass=f={50 + s['clarity'] / 10:.0f}"]
    if s["loudness"] > 100:
        f.append(f"acompressor=threshold=0.1:ratio={2 + s['loudness'] / 125:.1f}:makeup={1 + s['loudness'] / 250:.1f}")
    if s["echo"]:
        f.append(f"aecho=0.8:0.8:{40 + s['echo'] / 10:.0f}:0.4")
    if s["pitch"] != 500:
        k = 2 ** (((s["pitch"] - 500) / 500 * 8) / 12)      # slider 0..1000 = -8..+8 semitones
        f.append(f"asetrate=48000*{k:.4f},aresample=48000,atempo={1 / k:.4f}")   # atempo keeps the original speed
    if fx:
        f.append(fx)
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
    await run_ffmpeg("-i", STATE["audio"], "-vn", "-af", build_filter(SETTINGS, FX.get(STATE["mode"], "")), "-ar", "48000", "-ac", "2", out)
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
    F = MediaStream.Flags
    # explicit flags: no AUTO_DETECT probing needed for the audio side (falls back to the default if REQUIRED is missing)
    return MediaStream(path, AudioQuality.HIGH, audio_flags=getattr(F, "REQUIRED", getattr(F, "AUTO_DETECT", None)),
                       video_flags=F.IGNORE)


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
    mode = ALL_MODES[STATE["mode"]][0] if STATE["mode"] in ALL_MODES else "🎛 Custom"
    st = "▶️ playing" if STATE["playing"] and not STATE["paused"] else ("⏸ paused" if STATE["paused"] else "⏹ idle")
    return (f"🎙 <b>{sc('VC')}:</b> {vc}{sc(' 🟢 in call') if STATE['in_call'] else ''}\n🎵 <b>{sc('Audio')}:</b> {au}\n"
            f"🎚 <b>{sc('Mode')}:</b> {sc(mode)}\n"
            f"🔊 <b>{sc('Volume')}:</b> {STATE['volume']}%\n🔁 <b>{sc('Loop')}:</b> {sc('ON' if STATE['loop'] else 'off')}\n"
            f"📡 <b>{sc('State')}:</b> {sc(st)}")


def home_kb():
    return M([
        [btn(T("btn_join", "🎙 Join VC"), "join", "success"), btn(T("btn_leave", "🚪 Leave"), "leave", "danger")],
        [btn(T("btn_play", "▶️ Play"), "play", "success"), btn(T("btn_pause", "⏸ Pause"), "pause", "primary"),
         btn(T("btn_resume", "⏯ Resume"), "resume", "primary")],
        [btn(T("btn_modes", "🎚 Modes"), "modes", "primary"), btn("🎤 Voice", "voice", "success"),
         btn(T("btn_mixer", "🎛 Mixer"), "mix", "primary")],
        [btn("🔁 Loop: ON" if STATE["loop"] else "🔁 Loop: off", "loop", "success" if STATE["loop"] else "primary"),
         btn("🧪 Test tone", "tone", "primary"), btn("🛠 Admin", "adm:home", "primary")],
        [btn("🔉 50%", "vol:50", "primary"), btn("🔊 100%", "vol:100", "success"),
         btn("📢 150%", "vol:150", "primary"), btn("🔥 200%", "vol:200", "danger")],
        [btn(T("btn_support", "💬 Support"), url=f"https://t.me/{SUPPORT.lstrip('@')}")],
    ])


def _menu_kb(items, other_text, other_cb):
    items = list(items.items())
    rows = []
    for i in range(0, len(items), 2):
        rows.append([btn(("✅ " if STATE["mode"] == k else "") + v[0], f"mode:{k}", "success" if STATE["mode"] == k else "primary")
                     for k, v in items[i:i + 2]])
    rows.append([btn(other_text, other_cb, "success"), btn("⬅️ Back", "home", "danger")])
    return M(rows)


def modes_kb():
    return _menu_kb(MODES, "🎤 Voice filters", "voice")


def voice_kb():
    return _menu_kb(VOICE, "🎚 Sound modes", "modes")


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
    err = f"⚠️ <code>{html.escape(STATE['ub_error'][:300])}</code>\n" if STATE["ub_error"] and not C else ""
    if C and STATE["me"]:
        err = f"👤 {sc('Account')}: <code>{html.escape(STATE['me'])}</code>\n"
    return (f"🛠 <b>{sc('Admin panel')}</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"{sc('API')} {ok(c.get('api_id') and c.get('api_hash'))}   {sc('Session')} {ok(c.get('session'))}   {sc('Userbot')} {ok(C)}\n"
            + err +
            f"{sc('In call')} {ok(STATE['in_call'])}   {sc('Streaming')} {ok(STATE['streaming'])}   {sc('Loop')} {ok(STATE['loop'])}\n"
            f"{sc('Welcome')}: {sc('custom text') if c.get('welcome_text') else sc('theme default')} • "
            f"{sc('logo')} {ok(c.get('welcome_photo'))}\n"
            f"👑 {sc('Owners')}: <code>{', '.join(map(str, sorted(ADMINS))) or '—'}</code>\n"
            f"👥 {sc('Extra admins')}: <code>{', '.join(map(str, extra)) or '—'}</code>\n━━━━━━━━━━━━━━━━━━\n" + status_text())


def admin_kb():
    return M([
        [btn("🩺 Diagnose", "adm:diag", "primary"), btn("📜 Logs", "adm:logs", "primary")],
        [btn("🧪 Test tone", "tone", "success"), btn("🔁 Loop: ON" if STATE["loop"] else "🔁 Loop: off", "loop:adm",
                                                    "success" if STATE["loop"] else "primary")],
        [btn("🔄 Reload userbot", "adm:reload", "danger"), btn("🧹 Clear work", "adm:clear", "primary")],
        [btn("🔐 Account login", "adm:acct", "success"), btn("📖 Commands", "adm:cmds", "primary")],
        [btn("🗣 Speak text", "adm:say", "success"), btn("🌐 Voice: " + ("Hindi" if STATE["tts_lang"] == "hi" else "English (IN)"), "adm:lang", "primary")],
        [btn("🎨 Welcome setup", "adm:welcome", "primary"), btn("👥 Admins", "adm:admins", "primary")],
        [btn(T("btn_support", "💬 Support"), url=f"https://t.me/{SUPPORT.lstrip('@')}"), btn("🏠 Home", "home", "primary")],
    ])


def commands_text():
    rows = "\n".join(f"<code>{html.escape(u)}</code>\n   {html.escape(d)}" for _, u, d in COMMANDS)
    return (f"📖 <b>{sc('All commands')}</b>\n━━━━━━━━━━━━━━━━━━\n{rows}\n━━━━━━━━━━━━━━━━━━\n"
            f"💬 {sc('Support')}: {html.escape(SUPPORT)}")


def commands_kb():
    return M([[btn(T("btn_support", "💬 Support"), url=f"https://t.me/{SUPPORT.lstrip('@')}")],
              [btn("⬅️ Back", "adm:home", "primary")]])


def acct_text():
    c = cfg()
    ok = lambda x: "✅" if x else "❌"
    who = f"\n👤 <code>{html.escape(STATE['me'])}</code>" if C and STATE["me"] else ""
    err = f"\n⚠️ <code>{html.escape(STATE['ub_error'][:300])}</code>" if STATE["ub_error"] and not C else ""
    return (f"🔐 <b>{sc('Account login')}</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"{sc('API ID/HASH')} {ok(c.get('api_id') and c.get('api_hash'))}   {sc('Session')} {ok(c.get('session'))}   {sc('Userbot')} {ok(C)}{who}{err}\n\n"
            f"1️⃣ {sc('Set API ID/HASH')} (my.telegram.org)\n"
            f"2️⃣ {sc('Login with your phone number')}  <i>{sc('or')}</i>  {sc('paste a string session')}\n"
            f"{sc('The account must be a member of the group.')}")


def acct_kb():
    return M([
        [btn("🆔 Set API ID/HASH", "adm:papi", "primary")],
        [btn("📱 Phone login", "adm:plogin", "success"), btn("🔑 Paste session", "adm:psess", "success")],
        [btn("🧾 Session guide", "adm:sguide", "primary"), btn("🚪 Logout", "adm:logout", "danger")],
        [btn("🔄 Reload userbot", "adm:reload", "primary"), btn("⬅️ Back", "adm:home", "primary")],
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
            f"ffprobe <code>{html.escape(str(shutil.which('ffprobe') or 'MISSING'))}</code>\n"
            f"chat <code>{STATE['chat']}</code>  in_call <code>{STATE['in_call']}</code>  streaming <code>{STATE['streaming']}</code>\n"
            f"current file <code>{html.escape(os.path.basename(STATE['cur_out'] or '-'))}</code> ({STATE['cur_dur']:.1f}s)\n"
            f"work: <code>{html.escape(', '.join(files) or 'empty')}</code>")


def logs_text(n=25):
    lines = list(RING.buf)[-n:]
    body = html.escape("\n".join(lines))[-3500:] or "(no log lines yet)"
    return f"📜 <b>Last {len(lines)} log lines</b>\n<pre>{body}</pre>"


# ───────────────────────── commands ─────────────────────────
def support_kb():
    return M([[btn(T("btn_support", "💬 Support"), url=f"https://t.me/{SUPPORT.lstrip('@')}")]])


def welcome_text(name=""):
    """Priority: text set from the admin panel > theme file > built-in default. {store} and {name} are replaced."""
    raw = cfg().get("welcome_text") or T("welcome", DEFAULT_WELCOME)
    return raw.replace("{store}", html.escape(STORE)).replace("{name}", html.escape(name or ""))


def plain(text):
    return html.unescape(re.sub(r"<[^>]+>", "", text))


async def send_text(bot, chat_id, text, kb=None):
    try:
        return await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        log.exception("HTML message failed, sending plain text")
        return await bot.send_message(chat_id, plain(text), reply_markup=kb)


async def send_welcome(bot, chat_id, name="", kb=None):
    """Logo (if set) with the welcome text as caption; long text is sent as a normal message after the logo."""
    text, photo = welcome_text(name), cfg().get("welcome_photo")
    if photo:
        try:
            if len(text) <= 1000:
                return await bot.send_photo(chat_id, photo, caption=text, parse_mode="HTML", reply_markup=kb)
            await bot.send_photo(chat_id, photo)
        except Exception as e:
            log.error("welcome logo failed: %s: %s", type(e).__name__, e, exc_info=e)
    return await send_text(bot, chat_id, text, kb)


async def start(update: Update, ctx):
    name = (update.effective_user.first_name or "") if update.effective_user else ""
    chat_id = update.effective_chat.id
    if not is_admin(update):
        return await send_welcome(ctx.bot, chat_id, name, support_kb())
    if cfg().get("welcome_photo"):
        await send_welcome(ctx.bot, chat_id, name)
        await send_text(ctx.bot, chat_id, status_text(), home_kb())
    else:
        await send_text(ctx.bot, chat_id, f"{welcome_text(name)}\n\n{status_text()}", home_kb())


def welcome_panel_text():
    c = cfg()
    return (f"🎨 <b>{sc('Welcome setup')}</b>\n━━━━━━━━━━━━━━━━━━\n"
            f"✏️ {sc('Text')}: {sc('custom') if c.get('welcome_text') else sc('theme default')}\n"
            f"🖼 {sc('Logo')}: {'✅ ' + sc('set') if c.get('welcome_photo') else '❌ ' + sc('none')}\n\n"
            f"{sc('Placeholders')}: <code>{{store}}</code> {sc('store name')}, <code>{{name}}</code> {sc('visitor name')}\n"
            f"{sc('Bold / italic you apply in Telegram is kept.')}")


def welcome_kb():
    return M([
        [btn("✏️ Set text", "adm:wtext", "success"), btn("🖼 Set logo", "adm:wlogo", "success")],
        [btn("👁 Preview", "adm:wprev", "primary"), btn("🗑 Remove logo", "adm:wdellogo", "danger")],
        [btn("♻️ Reset all", "adm:wreset", "danger"), btn("⬅️ Back", "adm:home", "primary")],
    ])


async def setwelcome(update: Update, ctx):
    if not is_admin(update):
        return
    parts = (update.message.text_html or "").split(None, 1)
    if len(parts) < 2:
        PENDING[update.effective_user.id] = "text"
        return await update.message.reply_text(sc("Send the new welcome text now.") + "\n/cancel")
    await save_welcome_text(update, ctx, parts[1])


async def save_welcome_text(update, ctx, html_text):
    if len(html_text) > 3000:
        return await update.message.reply_text("❌ " + sc("Too long (max 3000 characters)."))
    c = cfg(); c["welcome_text"] = html_text; save_cfg(c)
    PENDING.pop(update.effective_user.id, None)
    await update.message.reply_text("✅ " + sc("Welcome text saved. Preview:"))
    await send_welcome(ctx.bot, update.effective_chat.id, update.effective_user.first_name or "")


async def setlogo(update: Update, ctx):
    if not is_admin(update):
        return
    PENDING[update.effective_user.id] = "logo"
    await update.message.reply_text("🖼 " + sc("Send the logo now as a Photo (not as a file).") + "\n/cancel")


async def cancel_cmd(update: Update, ctx):
    if is_admin(update):
        PENDING.pop(update.effective_user.id, None)
        await login_drop(update.effective_user.id)
        await update.message.reply_text("✖️ " + sc("Cancelled."))


async def got_text(update: Update, ctx):
    if not is_admin(update):
        return
    uid = update.effective_user.id
    if uid in LOGIN:
        return await login_step(update, ctx)
    kind = PENDING.get(uid)
    if kind == "text":
        return await save_welcome_text(update, ctx, update.message.text_html or "")
    txt = (update.message.text or "").strip()
    if kind == "session":
        ok, sess, why = analyse_session(txt)
        await try_delete(update)
        if not ok:
            return await update.effective_chat.send_message(f"❌ {why}.\n/sessionhelp  •  /cancel")
        PENDING.pop(uid, None)
        c = cfg(); c["session"] = sess; save_cfg(c)
        return await finish_login(update.effective_chat.id, "✅ Session saved.")
    if kind == "api":
        PENDING.pop(uid, None)
        await try_delete(update)
        m = re.fullmatch(r"(\d{3,12})[\s:,]+([0-9a-fA-F]{32})", txt)
        if not m:
            return await update.effective_chat.send_message("❌ Format: <code>12345678 0123456789abcdef0123456789abcdef</code>", parse_mode="HTML")
        c = cfg(); c.update(api_id=int(m.group(1)), api_hash=m.group(2)); save_cfg(c)
        return await update.effective_chat.send_message("✅ API saved. Ab 📱 Phone login ya 🔑 Paste session.")
    if kind == "tts":
        PENDING.pop(uid, None)
        return await speak(update.effective_chat.id, txt, STATE["tts_lang"])


async def try_delete(update):
    try:
        await update.message.delete()
    except Exception:
        pass


async def finish_login(chat_id, head):
    ok = await start_userbot()
    if ok:
        return await APP.bot.send_message(chat_id, f"{head}\n✅ Userbot ready: {STATE['me']}\nAb /join <group id> aur /testtone.")
    await APP.bot.send_message(chat_id, f"{head}\n❌ Userbot start nahi hua: {STATE['ub_error'] or 'see /logs'}")


async def do_logout():
    c = cfg(); c.pop("session", None); save_cfg(c)
    await stop_userbot()
    STATE["ub_error"] = ""


async def logout_cmd(update: Update, ctx):
    if is_admin(update):
        await do_logout()
        await update.message.reply_text("🚪 Account removed. /login ya /setsession se dobara login karo.")


# ── phone-number login (your own account, in this chat) ──
async def login_drop(uid):
    st = LOGIN.pop(uid, None)
    if st and st.get("client"):
        try:
            await st["client"].disconnect()
        except Exception:
            pass


async def login_begin(bot, chat_id, uid):
    c = cfg()
    if not (c.get("api_id") and c.get("api_hash")):
        return await bot.send_message(chat_id, "❌ Pehle API ID/HASH set karo: Admin → Account login → 🆔 Set API ID/HASH (ya /setapi).")
    await login_drop(uid)
    PENDING.pop(uid, None)
    LOGIN[uid] = dict(step="phone", ts=time.time(), client=None)
    await bot.send_message(chat_id, "📱 Apna phone number country code ke saath bhejo, jaise <code>+919876543210</code>\n/cancel", parse_mode="HTML")


async def login_cmd(update: Update, ctx):
    if is_admin(update):
        await login_begin(ctx.bot, update.effective_chat.id, update.effective_user.id)


async def login_step(update: Update, ctx):
    uid, chat = update.effective_user.id, update.effective_chat
    st = LOGIN[uid]
    txt = (update.message.text or "").strip()
    await try_delete(update)                          # phone / code / password never stay in the chat
    if time.time() - st["ts"] > LOGIN_TTL:
        await login_drop(uid)
        return await chat.send_message("⌛ Login time out. /login se dobara shuru karo.")
    st["ts"] = time.time()
    try:
        if st["step"] == "phone":
            phone = "+" + re.sub(r"\D", "", txt)
            if len(phone) < 8:
                return await chat.send_message("❌ Number galat. Aise bhejo: <code>+919876543210</code>", parse_mode="HTML")
            c = cfg()
            cl = st.get("client")
            if cl is None:                                 # reuse the connection when the number has to be re-typed
                cl = Client(f"login{uid}", api_id=int(c["api_id"]), api_hash=c["api_hash"], in_memory=True, no_updates=True)
                await cl.connect()
                st["client"] = cl
            sent = await cl.send_code(phone)
            st.update(step="code", phone=phone, hash=sent.phone_code_hash)
            return await chat.send_message(
                "📨 Code Telegram app (ya SMS) mein aaya hai.\nYahan <b>space ke saath</b> bhejo, jaise <code>1 2 3 4 5</code>. "
                "Seedha 12345 bhejoge to Telegram security ke liye login block kar sakta hai.\n/cancel", parse_mode="HTML")
        if st["step"] == "code":
            code = re.sub(r"\D", "", txt)
            if not 4 <= len(code) <= 7:
                return await chat.send_message("❌ Code 5 digit ka hota hai. Dobara bhejo: <code>1 2 3 4 5</code>", parse_mode="HTML")
            try:
                await st["client"].sign_in(st["phone"], st["hash"], code)
            except perr.SessionPasswordNeeded:
                st["step"] = "password"
                return await chat.send_message("🔒 Is account par 2-step password hai. Wo bhejo (message turant delete ho jayega).\n/cancel")
        elif st["step"] == "password":
            await st["client"].check_password(txt)
        sess = await st["client"].export_session_string()
        ok, sess, why = analyse_session(sess)
        await login_drop(uid)
        if not ok:
            return await chat.send_message(f"❌ Login hua par session format galat nikla: {why}")
        c = cfg(); c["session"] = sess; save_cfg(c)
        await finish_login(chat.id, "✅ Phone login ho gaya.")
    except perr.PhoneNumberInvalid:
        await chat.send_message("❌ Phone number invalid. Country code ke saath dobara bhejo: <code>+919876543210</code>", parse_mode="HTML")
    except perr.PhoneNumberBanned:
        await login_drop(uid); await chat.send_message("❌ Ye number Telegram par banned hai.")
    except perr.ApiIdInvalid:
        await login_drop(uid); await chat.send_message("❌ API ID/HASH galat hai. Admin → Account login → 🆔 se dobara set karo.")
    except perr.PhoneCodeInvalid:
        await chat.send_message("❌ Code galat. Dobara bhejo: <code>1 2 3 4 5</code>", parse_mode="HTML")
    except (perr.PhoneCodeExpired, perr.PhoneCodeEmpty):
        await login_drop(uid); await chat.send_message("⌛ Code expire ho gaya. /login se naya code mangao.")
    except perr.PasswordHashInvalid:
        await chat.send_message("❌ Password galat. Dobara bhejo.")
    except perr.FloodWait as e:
        await login_drop(uid); await chat.send_message(f"⏳ Telegram ne {getattr(e, 'value', '?')} second rukne ko kaha. Baad mein /login karo.")
    except Exception as e:
        await login_drop(uid)
        await fail(chat.id, "Phone login", e)


# ── Indian voice (text to speech) ──
def tts_make(text, lang):
    from gtts import gTTS                                     # pure-python wheel; needs internet at runtime
    out = os.path.join(WORK, "tts.mp3")
    gTTS(text=text, lang=lang, tld="co.in").save(out)
    return out


async def speak(chat_id, text, lang):
    text = (text or "").strip()[:300]
    if not text:
        return await APP.bot.send_message(chat_id, "Use: /say [hi|en] <text>")
    if not C or not STATE["chat"]:
        return await APP.bot.send_message(chat_id, "❌ Pehle userbot login karo aur /join <group id>.")
    try:
        path = await asyncio.wait_for(asyncio.get_running_loop().run_in_executor(None, tts_make, text, lang), 45)
        STATE.update(audio=path, audio_name="🗣 " + text[:30])
        await do_play()
        await APP.bot.send_message(chat_id, "🗣 Bol raha hoon…\n\n" + status_text(), parse_mode="HTML", reply_markup=home_kb())
    except ModuleNotFoundError:
        await fail(chat_id, "/say", RuntimeError("gTTS library nahi mili. Panel -> Scripts -> vcmusic -> Reinstall (requirements.txt mein gTTS hai)."))
    except Exception as e:
        await fail(chat_id, "/say", e)


async def say_cmd(update: Update, ctx):
    if not is_admin(update):
        return
    args = list(ctx.args)
    lang = STATE["tts_lang"]
    if args and args[0].lower() in ("hi", "en"):
        lang = args.pop(0).lower()
    if not args:
        PENDING[update.effective_user.id] = "tts"
        return await update.message.reply_text("🗣 " + sc("Send the text to speak (max 300 characters).") + "\n/cancel")
    await speak(update.effective_chat.id, " ".join(args), lang)


async def help_cmd(update: Update, ctx):
    if is_admin(update):
        await update.message.reply_text(commands_text(), parse_mode="HTML", reply_markup=commands_kb())
    else:
        await update.message.reply_text(f"💬 {sc('Support')}: {html.escape(SUPPORT)}", parse_mode="HTML", reply_markup=support_kb())


async def set_menu(app):
    """The '/' menu in Telegram: everyone sees /start and /help, admins see every command."""
    try:
        await app.bot.set_my_commands([BotCommand("start", "Home"), BotCommand("help", "Support")])
    except Exception:
        log.exception("set_my_commands (default) failed")
    full = [BotCommand(c, d[:200]) for c, _, d in COMMANDS]
    for uid in admin_ids():
        try:
            await app.bot.set_my_commands(full, scope=BotCommandScopeChat(uid))
        except Exception as e:
            log.warning("command menu for admin %s not set (they must press /start once): %s", uid, e)


async def got_photo(update: Update, ctx):
    if not is_admin(update):
        return
    m = update.message
    want = PENDING.get(update.effective_user.id) == "logo" or (m.caption or "").strip().lower().startswith("/setlogo")
    if not want or not m.photo:
        return
    c = cfg(); c["welcome_photo"] = m.photo[-1].file_id; save_cfg(c)
    PENDING.pop(update.effective_user.id, None)
    await m.reply_text("✅ " + sc("Logo saved. Preview:"))
    await send_welcome(ctx.bot, update.effective_chat.id, update.effective_user.first_name or "")


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
        i, h = int(ctx.args[0]), ctx.args[1].strip("'\"`")
        assert i > 0 and re.fullmatch(r"[0-9a-fA-F]{32}", h)
    except Exception:
        return await update.message.reply_text("Use: /setapi <api_id> <api_hash>\napi_id = number, api_hash = 32 letters/digits (my.telegram.org).")
    c = cfg(); c.update(api_id=i, api_hash=h); save_cfg(c)
    try:
        await update.message.delete()
    except Exception:
        pass
    await update.effective_chat.send_message("✅ API saved. Now /reload (or restart the bot).")


async def setsession(update: Update, ctx):
    if not is_admin(update):
        return
    if not ctx.args:
        return await update.message.reply_text("Use: /setsession <string_session>\nBanane ka tareeka: /sessionhelp")
    ok, s, why = analyse_session("".join(ctx.args))          # join: a long string may arrive with spaces/line breaks
    try:
        await update.message.delete()
    except Exception:
        pass
    if not ok:
        return await update.effective_chat.send_message(
            f"❌ Session save nahi hui: {why}.\n\nPyrogram 2.0.106 se nayi string banao: /sessionhelp\n(Purani working session delete nahi hui.)")
    c = cfg(); c["session"] = s; save_cfg(c)
    await update.effective_chat.send_message("✅ Session format sahi hai aur save ho gayi. Ab /reload bhejo.")


SESSION_SCRIPT = """pip install pyrogram==2.0.106
python3 - <<'EOF'
import asyncio
from pyrogram import Client
async def main():
    api_id = int(input("API ID: "))
    api_hash = input("API HASH: ").strip()
    async with Client("gen", api_id=api_id, api_hash=api_hash, in_memory=True) as app:
        print(await app.export_session_string())
asyncio.run(main())
EOF"""


def session_help_text():
    return ("🔑 <b>Valid string session kaise banaye</b>\nSabse aasan: /login se apne phone number se login karo.\n"
            "Ya PC / Termux par ye chalao (phone number, code, 2-step password dalo) aur jo lambi string aaye wo /setsession mein do.\n\n"
            "<pre>" + html.escape(SESSION_SCRIPT) + "</pre>\n"
            "• Wahi account lo jo group ka member hai.\n• Telethon / Pyrogram v1 / kisi bot ki string nahi chalegi.\n"
            "• Ye string account ka poora access hai - kisi ko mat do.")


async def sessionhelp(update: Update, ctx):
    if is_admin(update):
        await update.message.reply_text(session_help_text(), parse_mode="HTML")


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
    await m.edit_text("✅ Userbot ready" if ok else "❌ Userbot did not start\n" + (STATE["ub_error"] or "check /logs"))


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
    await set_menu(APP)


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
        if d == "voice":
            await show("🎤 <b>" + sc("Voice filters") + "</b>\n" + sc("Applied to the audio / voice you send me.") + "\n\n" + status_text(), voice_kb())
            return await q.answer()
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
                    await fail(chat_id, "Reload userbot", RuntimeError(STATE["ub_error"] or "did not start - see /logs"))
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
            if k == "welcome":
                await show(welcome_panel_text(), welcome_kb()); return await q.answer()
            if k == "wtext":
                PENDING[update.effective_user.id] = "text"
                await q.answer()
                return await q.message.reply_text("✏️ " + sc("Send the new welcome text now (you can use bold / italic).")
                                                  + "\n{store} / {name}\n/cancel")
            if k == "wlogo":
                PENDING[update.effective_user.id] = "logo"
                await q.answer()
                return await q.message.reply_text("🖼 " + sc("Send the logo now as a Photo (not as a file).") + "\n/cancel")
            if k == "wprev":
                await q.answer()
                return await send_welcome(ctx.bot, chat_id, update.effective_user.first_name or "")
            if k == "wdellogo":
                c = cfg(); c.pop("welcome_photo", None); save_cfg(c)
                await show(welcome_panel_text(), welcome_kb()); return await q.answer("Logo removed")
            if k == "wreset":
                c = cfg(); c.pop("welcome_photo", None); c.pop("welcome_text", None); save_cfg(c)
                await show(welcome_panel_text(), welcome_kb()); return await q.answer("Welcome reset")
            uid = update.effective_user.id
            if k == "cmds":
                await show(commands_text(), commands_kb()); return await q.answer()
            if k == "acct":
                await show(acct_text(), acct_kb()); return await q.answer()
            if k == "papi":
                PENDING[uid] = "api"; await q.answer()
                return await q.message.reply_text("🆔 " + sc("Send API ID and API HASH in one message:") + "\n<code>12345678 0123456789abcdef0123456789abcdef</code>\n/cancel", parse_mode="HTML")
            if k == "psess":
                PENDING[uid] = "session"; await q.answer()
                return await q.message.reply_text("🔑 " + sc("Paste your Pyrogram 2.x string session now.") + "\n/sessionhelp  •  /cancel")
            if k == "sguide":
                await q.answer(); return await q.message.reply_text(session_help_text(), parse_mode="HTML")
            if k == "plogin":
                await q.answer(); return await login_begin(ctx.bot, chat_id, uid)
            if k == "logout":
                await q.answer("Logging out…"); await do_logout()
                await show(acct_text(), acct_kb()); return
            if k == "say":
                PENDING[uid] = "tts"; await q.answer()
                return await q.message.reply_text("🗣 " + sc("Send the text to speak (max 300 characters).") + "\n/cancel")
            if k == "lang":
                STATE["tts_lang"] = "en" if STATE["tts_lang"] == "hi" else "hi"
                await show(admin_text(), admin_kb()); return await q.answer("Voice: " + ("Hindi" if STATE["tts_lang"] == "hi" else "Indian English"))
            if k == "admins":
                return await q.answer("Owners (ADMIN_IDS in panel) can use /addadmin <id> and /deladmin <id>.", show_alert=True)
        if d.startswith("mode:"):
            k = d.split(":")[1]
            if k in ALL_MODES:
                SETTINGS.clear(); SETTINGS.update(ALL_MODES[k][1]); STATE["mode"] = k
            if k in VOICE:
                await show("🎤 <b>" + sc("Voice filters") + "</b>\n\n" + status_text(), voice_kb())
            else:
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
    STATE.update(in_call=False, streaming=False, playing=False, paused=False, me="")


async def start_userbot():
    global U, C
    await stop_userbot()
    c = cfg()
    STATE["ub_error"] = ""
    if not (c.get("api_id") and c.get("api_hash") and c.get("session")):
        log.info("Waiting for /setapi and /setsession from the admin")
        STATE["ub_error"] = "API ID / API HASH / session abhi set nahi hain (/setapi, /setsession)"
        return False
    ok, sess, why = analyse_session(c["session"])
    if not ok:
        STATE["ub_error"] = f"Saved session galat hai: {why}. /sessionhelp se nayi banao, phir /setsession aur /reload"
        log.error("Userbot not started: %s", STATE["ub_error"])
        return False
    try:
        U = Client("vcuser", api_id=int(c["api_id"]), api_hash=c["api_hash"], session_string=sess, workdir=CWD)
        await U.start()
        await warm_peers()
        C = PyTgCalls(U)
        await C.start()
        try:
            me = await U.get_me()
            STATE["me"] = f"{me.first_name or ''} ({me.id})"
        except Exception:
            STATE["me"] = ""
        log.info("Userbot ready (py-tgcalls %s, ntgcalls %s, pyrogram %s)", ver("py-tgcalls"), ver("ntgcalls"), ver("pyrogram"))
        return True
    except Exception as e:
        log.error("Userbot start failed: %s: %s", type(e).__name__, e, exc_info=e)
        STATE["ub_error"] = f"{type(e).__name__}: {str(e)[:200]}"
        U = C = None
        return False


async def post_init(app):
    global APP
    APP = app
    await asyncio.get_running_loop().run_in_executor(None, ensure_ffprobe)
    await set_menu(app)
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
                     ("addadmin", addadmin), ("deladmin", deladmin), ("setwelcome", setwelcome),
                     ("setlogo", setlogo), ("cancel", cancel_cmd), ("sessionhelp", sessionhelp),
                     ("login", login_cmd), ("logout", logout_cmd), ("say", say_cmd), ("help", help_cmd)):
        app.add_handler(CommandHandler(name, fn))
    app.add_handler(MessageHandler((filters.VOICE | filters.AUDIO | filters.Document.AUDIO) & filters.ChatType.PRIVATE, got_audio))
    app.add_handler(MessageHandler(filters.PHOTO & filters.ChatType.PRIVATE, got_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, got_text))
    app.add_handler(CallbackQueryHandler(cb))
    app.add_error_handler(on_error)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
