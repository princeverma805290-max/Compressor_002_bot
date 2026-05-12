"""
Advanced Telegram Video Compressor Bot
- Telethon (no file size limit - 1GB+)
- pyTelegramBotAPI for commands/UI
- Progress: Download / Compress / Upload
- Resolution: 144p 360p 480p 720p 1080p
"""

import os, re, time, logging, threading, asyncio
from pathlib import Path
import requests
import telebot
from telebot import types
from telethon import TelegramClient
from telethon.tl.types import DocumentAttributeVideo

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
OWNER_ID     = int(os.environ.get("OWNER_ID", "0"))
API_ID       = int(os.environ.get("API_ID", "0"))
API_HASH     = os.environ.get("API_HASH", "")
DOWNLOAD_DIR = Path("downloads")
OUTPUT_DIR   = Path("outputs")
DOWNLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="Markdown")

# Telethon client (bot login)
tg_client = TelegramClient("bot_session", API_ID, API_HASH)

RESOLUTIONS = {
    "144p":  {"h": 144,  "w": 256,  "crf": 40, "audio": "64k",  "emoji": "📱"},
    "360p":  {"h": 360,  "w": 640,  "crf": 36, "audio": "96k",  "emoji": "📺"},
    "480p":  {"h": 480,  "w": 854,  "crf": 32, "audio": "112k", "emoji": "🖥️"},
    "720p":  {"h": 720,  "w": 1280, "crf": 28, "audio": "128k", "emoji": "🔵"},
    "1080p": {"h": 1080, "w": 1920, "crf": 24, "audio": "192k", "emoji": "🟣"},
}

user_data = {}

def get_ud(uid):
    if uid not in user_data:
        user_data[uid] = {}
    return user_data[uid]

def clear_ud(uid):
    for k in ("video_path", "thumbnail_path"):
        p = user_data.get(uid, {}).pop(k, None)
        if p:
            try: Path(p).unlink(missing_ok=True)
            except: pass
    user_data.pop(uid, None)

# ── Helpers ───────────────────────────────────────────────────────────────────
def make_bar(pct, w=16):
    f = int(w * min(pct, 100) / 100)
    return "█" * f + "░" * (w - f)

def fmt_size(b):
    b = int(b)
    if b < 1024:    return f"{b} B"
    if b < 1<<20:   return f"{b/1024:.1f} KB"
    if b < 1<<30:   return f"{b/(1<<20):.1f} MB"
    return f"{b/(1<<30):.2f} GB"

def fmt_time(s):
    s = int(max(0, s))
    if s < 60:   return f"{s}s"
    if s < 3600: return f"{s//60}m {s%60:02d}s"
    return f"{s//3600}h {(s%3600)//60:02d}m"

def fmt_speed(bps):
    if bps < 1024:  return f"{bps:.0f} B/s"
    if bps < 1<<20: return f"{bps/1024:.1f} KB/s"
    return f"{bps/(1<<20):.1f} MB/s"

def safe_edit(msg, text):
    try:
        bot.edit_message_text(text, msg.chat.id, msg.message_id, parse_mode="Markdown")
    except Exception:
        pass

def is_owner(uid):
    return OWNER_ID == 0 or uid == OWNER_ID

def res_keyboard():
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("📱 144p  — Sabse chhota", callback_data="res_144p"))
    kb.add(types.InlineKeyboardButton("📺 360p  — Chhota size",  callback_data="res_360p"))
    kb.add(types.InlineKeyboardButton("🖥️ 480p  — Medium SD",    callback_data="res_480p"))
    kb.add(types.InlineKeyboardButton("🔵 720p  — HD ✅",         callback_data="res_720p"))
    kb.add(types.InlineKeyboardButton("🟣 1080p — Full HD",       callback_data="res_1080p"))
    return kb

def skip_kb(cb_data, label):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton(f"⏭️ Skip {label}", callback_data=cb_data))
    return kb

# ── Telethon Download with progress ──────────────────────────────────────────
async def telethon_download(chat_id, message_id, dest, status_msg, total_size):
    start = time.time()
    last  = start
    done  = [0]

    def progress_cb(current, total):
        done[0] = current
        now = time.time()
        nonlocal last
        if now - last < 2: return
        last = now
        el  = now - start
        sp  = current / el if el else 0
        pct = min(current / total * 100, 99) if total else 0
        eta = (total - current) / sp if sp and total else 0
        safe_edit(status_msg,
            f"⬇️ *Downloading Video*\n\n"
            f"`[{make_bar(pct)}]` `{pct:.1f}%`\n\n"
            f"📥 `{fmt_size(current)}`" +
            (f" / `{fmt_size(total)}`" if total else "") +
            f"\n⚡ Speed:   `{fmt_speed(sp)}`\n"
            f"⏱️ Elapsed: `{fmt_time(el)}`\n"
            f"⏳ ETA:     `{fmt_time(eta)}`"
        )

    async with tg_client:
        msg = await tg_client.get_messages(chat_id, ids=message_id)
        await tg_client.download_media(msg, file=str(dest), progress_callback=progress_cb)

    elapsed = time.time() - start
    return elapsed, done[0] or total_size

# ── Sync wrapper for telethon download ───────────────────────────────────────
def download_with_telethon(chat_id, message_id, dest, status_msg, total_size, loop):
    return asyncio.run_coroutine_threadsafe(
        telethon_download(chat_id, message_id, dest, status_msg, total_size),
        loop
    ).result()

# ── Get duration ──────────────────────────────────────────────────────────────
def get_duration(path):
    try:
        import subprocess
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True)
        return float(r.stdout.strip())
    except: return 0.0

# ── Compress with progress ────────────────────────────────────────────────────
def compress_with_progress(cmd, dur, status_msg, res_key, crf, audio_br):
    import subprocess
    start  = time.time()
    buf    = []
    cur = fps = spd = 0.0
    brate  = ""
    proc = subprocess.Popen(cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
                            universal_newlines=True)
    last_upd = start
    for line in proc.stderr:
        line = line.strip()
        buf.append(line)
        m = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", line)
        if m: h,mi,s = m.groups(); cur = int(h)*3600+int(mi)*60+float(s)
        m = re.search(r"fps=\s*([\d.]+)", line)
        if m: fps = float(m.group(1))
        m = re.search(r"speed=\s*([\d.]+)x", line)
        if m: spd = float(m.group(1))
        m = re.search(r"bitrate=\s*([\d.]+\s*\S+bits/s)", line)
        if m: brate = m.group(1).strip()
        now = time.time()
        if now - last_upd >= 3:
            el  = now - start
            pct = min(cur / dur * 100, 99) if dur else 0
            eta = (dur - cur) / spd if spd and dur else 0
            safe_edit(status_msg,
                f"⚙️ *Compressing — {res_key}*\n\n"
                f"`[{make_bar(pct)}]` `{pct:.1f}%`\n\n"
                f"🎞️ Processed: `{fmt_time(cur)}` / `{fmt_time(dur)}`\n"
                f"🔢 FPS:       `{fps:.1f}`\n"
                f"⚡ Speed:    `{spd:.2f}x realtime`\n"
                f"📡 Bitrate:  `{brate or '—'}`\n"
                f"⏱️ Elapsed:  `{fmt_time(el)}`\n"
                f"⏳ ETA:      `{fmt_time(eta)}`\n\n"
                f"CRF `{crf}` | Audio `{audio_br}`"
            )
            last_upd = now
    proc.wait()
    return proc.returncode, "\n".join(buf), time.time() - start

# ── Telethon Upload with progress ─────────────────────────────────────────────
async def telethon_upload(chat_id, reply_to_id, output_path, caption, thumb_path,
                           status_msg, res_key, orig_size, comp_size):
    fsz       = output_path.stat().st_size
    start     = time.time()
    last      = start
    reduction = (orig_size - comp_size) / orig_size * 100 if orig_size else 0
    SPEED     = 1.5 * (1 << 20)

    def progress_cb(current, total):
        nonlocal last
        now = time.time()
        if now - last < 2: return
        last = now
        el  = now - start
        sp  = current / el if el else SPEED
        pct = min(current / total * 100, 97) if total else 50
        eta = (total - current) / sp if sp else 0
        safe_edit(status_msg,
            f"⬆️ *Uploading — {res_key}*\n\n"
            f"`[{make_bar(pct)}]` `{pct:.1f}%`\n\n"
            f"📦 Size:     `{fmt_size(fsz)}`\n"
            f"⚡ Speed:   `{fmt_speed(sp)}`\n"
            f"⏱️ Elapsed: `{fmt_time(el)}`\n"
            f"⏳ ETA:     `~{fmt_time(eta)}`\n\n"
            f"📉 Saved `{reduction:.1f}%`"
        )

    thumb = thumb_path if thumb_path and Path(thumb_path).exists() else None

    async with tg_client:
        await tg_client.send_file(
            chat_id,
            str(output_path),
            caption=caption,
            thumb=thumb,
            supports_streaming=True,
            reply_to=reply_to_id,
            progress_callback=progress_cb,
            parse_mode="md",
        )

    return time.time() - start

def upload_with_telethon(chat_id, reply_to_id, output_path, caption, thumb_path,
                          status_msg, res_key, orig_size, comp_size, loop):
    return asyncio.run_coroutine_threadsafe(
        telethon_upload(chat_id, reply_to_id, output_path, caption, thumb_path,
                        status_msg, res_key, orig_size, comp_size),
        loop
    ).result()

# ── Pipeline ──────────────────────────────────────────────────────────────────
def run_pipeline(message, uid, loop):
    ud        = get_ud(uid)
    tpath     = ud.get("thumbnail_path")
    title     = ud.get("title")
    res_key   = ud.get("resolution", "720p")
    preset    = ud.get("preset", RESOLUTIONS["720p"])
    orig_size = ud.get("total_size", 0)
    msg_id    = ud.get("msg_id")
    chat_id   = ud.get("chat_id")
    h, w, crf, abr = preset["h"], preset["w"], preset["crf"], preset["audio"]

    ext  = ud.get("ext", ".mp4")
    dest = DOWNLOAD_DIR / f"{uid}_{int(time.time())}_input{ext}"
    outp = OUTPUT_DIR   / f"{uid}_{int(time.time())}_{res_key}.mp4"

    # ── Download ──────────────────────────────────────────────────────────────
    st = bot.send_message(message.chat.id,
        f"⬇️ *Download shuru...*\n\n📁 `{fmt_size(orig_size)}`\n"
        f"`[░░░░░░░░░░░░░░░░]` `0%`", parse_mode="Markdown")
    try:
        elapsed, dl = download_with_telethon(chat_id, msg_id, dest, st, orig_size, loop)
        sp = dl / elapsed if elapsed else 0
        safe_edit(st,
            f"✅ *Download Complete!*\n\n"
            f"`[████████████████]` `100%`\n\n"
            f"📥 `{fmt_size(dl)}`\n"
            f"⚡ Avg Speed: `{fmt_speed(sp)}`\n"
            f"⏱️ Time: `{fmt_time(elapsed)}`")
    except Exception as e:
        safe_edit(st, f"❌ Download failed!\n`{e}`")
        clear_ud(uid); return

    # ── Compress ──────────────────────────────────────────────────────────────
    dur  = get_duration(str(dest))
    cmsg = bot.send_message(message.chat.id,
        f"⚙️ *Compression shuru...*\n\n📐 `{res_key}` ({w}×{h}) | CRF `{crf}`\n"
        f"`[░░░░░░░░░░░░░░░░]` `0%`", parse_mode="Markdown")

    scale = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
             f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1")
    cmd = ["ffmpeg", "-i", str(dest), "-vf", scale,
           "-c:v", "libx264", "-crf", str(crf), "-preset", "ultrafast",
           "-c:a", "aac", "-b:a", abr,
           "-movflags", "+faststart", "-y", str(outp)]

    rc, stderr, celapsed = compress_with_progress(cmd, dur, cmsg, res_key, crf, abr)
    try: dest.unlink(missing_ok=True)
    except: pass

    if rc != 0:
        safe_edit(cmsg, f"❌ *Compression failed!*\n\n```\n{stderr[-400:]}\n```")
        clear_ud(uid); return

    csz       = outp.stat().st_size
    reduction = (orig_size - csz) / orig_size * 100 if orig_size else 0
    safe_edit(cmsg,
        f"✅ *Compression Complete!*\n\n"
        f"`[████████████████]` `100%`\n\n"
        f"📐 `{res_key}` ({w}×{h})\n"
        f"📁 Original:   `{fmt_size(orig_size)}`\n"
        f"📦 Compressed: `{fmt_size(csz)}`\n"
        f"📉 Saved:      `{reduction:.1f}%`\n"
        f"⏱️ Time:       `{fmt_time(celapsed)}`")

    # ── Upload ────────────────────────────────────────────────────────────────
    umsg = bot.send_message(message.chat.id,
        f"⬆️ *Upload shuru...*\n\n📦 `{fmt_size(csz)}`\n"
        f"`[░░░░░░░░░░░░░░░░]` `0%`", parse_mode="Markdown")

    caption = title or (f"🎬 *{res_key} Compressed*\n"
                        f"📉 `{reduction:.1f}%` reduced | 📐 `{w}×{h}` | 📦 `{fmt_size(csz)}`")
    try:
        uel = upload_with_telethon(
            message.chat.id, message.message_id,
            outp, caption, tpath, umsg,
            res_key, orig_size, csz, loop)
        safe_edit(umsg,
            f"✅ *Upload Complete!*\n\n"
            f"`[████████████████]` `100%`\n\n"
            f"📦 `{fmt_size(csz)}`\n"
            f"⏱️ Upload: `{fmt_time(uel)}`\n"
            f"⏱️ Total:  `{fmt_time(celapsed + uel)}`\n\n"
            f"🎉 *Done! {res_key} video ready!*")
    except Exception as e:
        logger.error(f"Upload error: {e}", exc_info=True)
        safe_edit(umsg, f"❌ Upload failed!\n`{e}`")
    finally:
        clear_ud(uid)
        try: outp.unlink(missing_ok=True)
        except: pass

# ── Handlers ──────────────────────────────────────────────────────────────────
@bot.message_handler(commands=["start"])
def cmd_start(msg):
    if not is_owner(msg.from_user.id):
        bot.reply_to(msg, "❌ Unauthorized!"); return
    bot.reply_to(msg,
        f"👋 Welcome *{msg.from_user.first_name}*!\n\n"
        "🎬 *Advanced Video Compressor Bot*\n\n"
        "⬇️ Download → ⚙️ Compress → ⬆️ Upload\n"
        "Har step ka real-time progress bar!\n\n"
        "📐 *Resolutions:* 144p / 360p / 480p / 720p / 1080p\n"
        "🖼️ Thumbnail | ✏️ Title | 🔐 Owner only\n"
        "📦 *1GB tak ki files support!*\n\n"
        "📤 *Video bhejein shuru karne ke liye!*\n"
        "/help | /cancel")

@bot.message_handler(commands=["help"])
def cmd_help(msg):
    if not is_owner(msg.from_user.id): return
    bot.reply_to(msg,
        "📖 *Help*\n\n"
        "1️⃣ Video bhejein (max 1GB)\n"
        "2️⃣ Resolution choose karein\n"
        "3️⃣ Thumbnail ya skip\n"
        "4️⃣ Title ya skip\n"
        "5️⃣ Progress bars dekhein!\n\n"
        "📱 144p | 📺 360p | 🖥️ 480p | 🔵 720p | 🟣 1080p")

@bot.message_handler(commands=["cancel"])
def cmd_cancel(msg):
    clear_ud(msg.from_user.id)
    bot.reply_to(msg, "❌ Cancel kar diya!")

@bot.message_handler(content_types=["video", "document"])
def handle_video(msg):
    uid = msg.from_user.id
    if not is_owner(uid):
        bot.reply_to(msg, "❌ Unauthorized!"); return
    video = msg.video or msg.document
    if not video:
        bot.reply_to(msg, "❗ Sirf video bhejein!"); return
    if msg.document:
        mime = getattr(msg.document, "mime_type", "") or ""
        if not mime.startswith("video/"):
            bot.reply_to(msg, "❗ Sirf video files bhejein!"); return
    total = getattr(video, "file_size", 0) or 0
    if total > 1 * (1 << 30):
        bot.reply_to(msg, "❗ Max 1GB!"); return

    ext = ".mp4"
    if msg.document:
        fname = getattr(video, "file_name", None) or "video.mp4"
        ext   = Path(fname).suffix or ".mp4"

    ud = get_ud(uid)
    ud.update({
        "thumbnail_path": None,
        "title":          None,
        "total_size":     total,
        "last_msg":       msg,
        "msg_id":         msg.message_id,
        "chat_id":        msg.chat.id,
        "ext":            ext,
    })

    kb = res_keyboard()
    bot.reply_to(msg,
        f"📁 File: `{fmt_size(total)}`\n\n"
        "🎚️ *Resolution select karein:*\n\n"
        "📱 *144p* — Sabse chhota\n📺 *360p* — Chhota\n"
        "🖥️ *480p* — SD\n🔵 *720p* — HD *(Best)*\n🟣 *1080p* — Full HD",
        parse_mode="Markdown", reply_markup=kb)

@bot.callback_query_handler(func=lambda c: c.data.startswith("res_"))
def cb_resolution(call):
    uid     = call.from_user.id
    res_key = call.data.replace("res_", "")
    preset  = RESOLUTIONS[res_key]
    ud      = get_ud(uid)
    ud["resolution"] = res_key
    ud["preset"]     = preset
    bot.answer_callback_query(call.id)
    bot.edit_message_text(
        f"✅ *Resolution:* `{res_key}` {preset['emoji']} `{preset['w']}×{preset['h']}` | CRF `{preset['crf']}`",
        call.message.chat.id, call.message.message_id, parse_mode="Markdown")
    bot.send_message(call.message.chat.id,
        "🖼️ *Thumbnail* (optional)\nImage bhejein ya skip:",
        parse_mode="Markdown", reply_markup=skip_kb("skip_thumbnail", "Thumbnail"))

@bot.callback_query_handler(func=lambda c: c.data == "skip_thumbnail")
def cb_skip_thumb(call):
    uid = call.from_user.id
    get_ud(uid)["thumbnail_path"] = None
    bot.answer_callback_query(call.id)
    bot.edit_message_text("⏭️ Thumbnail skip!", call.message.chat.id, call.message.message_id)
    bot.send_message(call.message.chat.id,
        "✏️ *Title* (optional)\nText likhein ya skip:",
        parse_mode="Markdown", reply_markup=skip_kb("skip_title", "Title"))

@bot.message_handler(content_types=["photo"])
def handle_thumbnail(msg):
    uid = msg.from_user.id
    ud  = get_ud(uid)
    if "resolution" not in ud or "thumbnail_path" not in ud: return
    if ud.get("thumbnail_path") is not None: return
    st = bot.reply_to(msg, "⬇️ Thumbnail download...")
    try:
        fobj = msg.photo[-1]
        fi   = bot.get_file(fobj.file_id)
        url  = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{fi.file_path}"
        path = DOWNLOAD_DIR / f"{uid}_{int(time.time())}_thumb.jpg"
        path.write_bytes(requests.get(url).content)
        ud["thumbnail_path"] = str(path)
        safe_edit(st, "✅ Thumbnail set!")
        bot.send_message(msg.chat.id, "✏️ *Title* (optional)\nText likhein ya skip:",
            parse_mode="Markdown", reply_markup=skip_kb("skip_title", "Title"))
    except Exception as e:
        ud["thumbnail_path"] = None
        safe_edit(st, f"⚠️ Thumbnail skip (error: {e})")
        bot.send_message(msg.chat.id, "✏️ *Title* (optional)\nText likhein ya skip:",
            parse_mode="Markdown", reply_markup=skip_kb("skip_title", "Title"))

@bot.callback_query_handler(func=lambda c: c.data == "skip_title")
def cb_skip_title(call):
    uid  = call.from_user.id
    ud   = get_ud(uid)
    ud["title"] = None
    bot.answer_callback_query(call.id)
    bot.edit_message_text("⏭️ Title skip!", call.message.chat.id, call.message.message_id)
    msg  = ud.get("last_msg", call.message)
    loop = asyncio.get_event_loop()
    threading.Thread(target=run_pipeline, args=(msg, uid, loop), daemon=True).start()

@bot.message_handler(content_types=["text"])
def handle_title(msg):
    uid = msg.from_user.id
    ud  = get_ud(uid)
    if "resolution" not in ud: return
    if "msg_id" not in ud: return
    if ud.get("title") is not None: return
    text = msg.text.strip()
    if text.startswith("/"): return
    ud["title"] = text
    bot.reply_to(msg, f"✅ Title: _{text}_", parse_mode="Markdown")
    src  = ud.get("last_msg", msg)
    loop = asyncio.get_event_loop()
    threading.Thread(target=run_pipeline, args=(src, uid, loop), daemon=True).start()

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN set nahi!"); return
    if not API_ID or not API_HASH:
        logger.error("API_ID aur API_HASH set karo Render environment variables mein!"); return

    logger.info(f"Owner ID: {OWNER_ID if OWNER_ID else 'Not set (open)'}")

    # Start asyncio loop in background thread
    loop = asyncio.new_event_loop()
    def start_loop(loop):
        asyncio.set_event_loop(loop)
        loop.run_forever()
    t = threading.Thread(target=start_loop, args=(loop,), daemon=True)
    t.start()

    # Connect telethon client
    async def connect_client():
        await tg_client.start(bot_token=BOT_TOKEN)
        logger.info("✅ Telethon client connected!")
    asyncio.run_coroutine_threadsafe(connect_client(), loop).result()

    logger.info("✅ Bot polling shuru...")
    bot.infinity_polling(timeout=60, long_polling_timeout=30)

if __name__ == "__main__":
    main()
