"""
VC Bridge Bot: Group A ke VC ka audio -> ffmpeg mix -> Group B ke VC mein.
Controls Telegram inline panel se (Gain/Loudness/Bass/Treble/Clarity/Echo/Pitch).

Install:
  pip install pyrogram tgcrypto "py-tgcalls==2.1.0"   # version pin zaroori
  apt install ffmpeg
.env / config neeche CONFIG mein bharo.
Run: python bridge.py
"""
import asyncio, os, subprocess
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pytgcalls import PyTgCalls
from pytgcalls.types import MediaStream, AudioQuality, RecordStream

CONFIG = dict(
    API_ID=int(os.getenv("API_ID", "0")),
    API_HASH=os.getenv("API_HASH", ""),
    BOT_TOKEN=os.getenv("BOT_TOKEN", ""),
    SESSION_A=os.getenv("SESSION_A", ""),   # userbot string session jo Group A VC mein rahega
    SESSION_B=os.getenv("SESSION_B", ""),   # userbot string session jo Group B VC mein rahega
    OWNER_IDS=[int(x) for x in os.getenv("OWNER_IDS", "0").split(",")],
    FIFO="/tmp/vc_bridge_out.raw",
)

SETTINGS = dict(gain=100, loudness=100, bass=100, treble=100, clarity=100, echo=0, pitch=500)
STATE = dict(chat_a=None, chat_b=None, ff=None)

bot = Client("panel", CONFIG["API_ID"], CONFIG["API_HASH"], bot_token=CONFIG["BOT_TOKEN"])
ua = Client("ua", CONFIG["API_ID"], CONFIG["API_HASH"], session_string=CONFIG["SESSION_A"])
ub = Client("ub", CONFIG["API_ID"], CONFIG["API_HASH"], session_string=CONFIG["SESSION_B"])
call_a, call_b = PyTgCalls(ua), PyTgCalls(ub)


def build_filter(s):
    f = [f"volume={1 + s['gain'] / 100:.2f}",
         f"bass=g={s['bass'] / 50:.1f}",
         f"treble=g={s['treble'] / 50:.1f}",
         f"highpass=f={50 + s['clarity'] / 10:.0f}"]
    if s["loudness"] > 100:
        f.append(f"acompressor=threshold=0.1:ratio={2 + s['loudness'] / 125:.1f}:makeup={1 + s['loudness'] / 250:.1f}")
    if s["echo"]:
        f.append(f"aecho=0.8:0.8:{40 + s['echo'] / 10:.0f}:0.4")
    if s["pitch"] != 500:
        f.append(f"asetrate=48000*{1 + (s['pitch'] - 500) / 2500:.2f},aresample=48000")
    f.append("alimiter=limit=0.9")  # hard safety cap: distortion/kaan phatne se bachata hai
    return ",".join(f)


def start_ffmpeg():
    if STATE["ff"]:
        STATE["ff"].kill()
    if not os.path.exists(CONFIG["FIFO"]):
        os.mkfifo(CONFIG["FIFO"])
    STATE["ff"] = subprocess.Popen(
        ["ffmpeg", "-loglevel", "quiet", "-f", "s16le", "-ar", "48000", "-ac", "1", "-i", "pipe:0",
         "-af", build_filter(SETTINGS), "-f", "s16le", "-ar", "48000", "-ac", "2", "-y", CONFIG["FIFO"]],
        stdin=subprocess.PIPE)


@call_a.on_update()
async def on_frames(_, update):
    # A ke VC ke raw PCM frames -> ffmpeg
    frames = getattr(update, "frames", None)
    if not frames or not STATE["ff"] or STATE["ff"].poll() is not None:
        return
    for fr in frames:
        try:
            STATE["ff"].stdin.write(fr.frame)
        except BrokenPipeError:
            break


def panel():
    rows = []
    for k, v in SETTINGS.items():
        rows.append([InlineKeyboardButton("➖", callback_data=f"{k}:-100"),
                     InlineKeyboardButton(f"{k.title()}: {v}/1000", callback_data="noop"),
                     InlineKeyboardButton("➕", callback_data=f"{k}:+100")])
    rows.append([InlineKeyboardButton("🔄 Reset", callback_data="reset")])
    return InlineKeyboardMarkup(rows)


def owner(_, __, m):
    uid = getattr(m, "from_user", None)
    return bool(uid) and uid.id in CONFIG["OWNER_IDS"]


is_owner = filters.create(owner)


@bot.on_message(filters.command("panel") & is_owner)
async def show_panel(_, m):
    await m.reply("🎛 CUSTOM MIX", reply_markup=panel())


@bot.on_message(filters.command("bridge") & is_owner)
async def bridge(_, m):
    # /bridge <chat_id_A> <chat_id_B>
    try:
        a, b = int(m.command[1]), int(m.command[2])
    except Exception:
        return await m.reply("Use: /bridge <group_A_id> <group_B_id>")
    STATE["chat_a"], STATE["chat_b"] = a, b
    start_ffmpeg()
    await call_a.play(a)  # A ke VC mein join (silent)
    await call_a.record(a, RecordStream(True, AudioQuality.HIGH))
    await call_b.play(b, MediaStream(CONFIG["FIFO"], AudioQuality.HIGH,
                                     ffmpeg_parameters="-f s16le -ar 48000 -ac 2"))
    await m.reply("✅ Bridge ON")


@bot.on_message(filters.command("stop") & is_owner)
async def stop(_, m):
    for c, chat in ((call_a, STATE["chat_a"]), (call_b, STATE["chat_b"])):
        if chat:
            try:
                await c.leave_call(chat)
            except Exception:
                pass
    if STATE["ff"]:
        STATE["ff"].kill()
    await m.reply("⏹ Bridge OFF")


@bot.on_callback_query(is_owner)
async def cb(_, q):
    if q.data == "noop":
        return await q.answer()
    if q.data == "reset":
        SETTINGS.update(gain=100, loudness=100, bass=100, treble=100, clarity=100, echo=0, pitch=500)
    else:
        k, d = q.data.split(":")
        SETTINGS[k] = max(0, min(1000, SETTINGS[k] + int(d)))
    if STATE["chat_a"]:
        start_ffmpeg()  # naya filter live apply
    await q.message.edit_reply_markup(panel())
    await q.answer("Updated")


async def main():
    await asyncio.gather(bot.start(), ua.start(), ub.start())
    await call_a.start()
    await call_b.start()
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
