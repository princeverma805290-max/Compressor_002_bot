"""
Advanced Telegram Video Compressor Bot
- Real download/compression/upload progress bars
- Resolution: 144p / 360p / 480p / 720p / 1080p
- Custom Thumbnail + Title
- Owner-only access
- Compatible with python-telegram-bot 20.7
"""

import os
import re
import asyncio
import logging
import time
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)
from telegram.error import RetryAfter, BadRequest

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────────────────
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
OWNER_ID     = int(os.environ.get("OWNER_ID", "0"))   # Set in Render env vars
DOWNLOAD_DIR = Path("downloads")
OUTPUT_DIR   = Path("outputs")
DOWNLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# ── States ───────────────────────────────────────────────────────────────────
WAITING_VIDEO     = 1
WAITING_QUALITY   = 2
WAITING_THUMBNAIL = 3
WAITING_TITLE     = 4

# ── Resolution presets ────────────────────────────────────────────────────────
RESOLUTIONS = {
    "144p":  {"h": 144,  "w": 256,  "crf": 40, "audio": "64k",  "emoji": "📱"},
    "360p":  {"h": 360,  "w": 640,  "crf": 36, "audio": "96k",  "emoji": "📺"},
    "480p":  {"h": 480,  "w": 854,  "crf": 32, "audio": "112k", "emoji": "🖥️"},
    "720p":  {"h": 720,  "w": 1280, "crf": 28, "audio": "128k", "emoji": "🔵"},
    "1080p": {"h": 1080, "w": 1920, "crf": 24, "audio": "192k", "emoji": "🟣"},
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def make_bar(pct: float, width: int = 16) -> str:
    filled = int(width * min(pct, 100) / 100)
    return "█" * filled + "░" * (width - filled)

def fmt_size(b: int) -> str:
    if b < 1024:      return f"{b} B"
    if b < 1024**2:   return f"{b/1024:.1f} KB"
    if b < 1024**3:   return f"{b/1024**2:.1f} MB"
    return f"{b/1024**3:.2f} GB"

def fmt_time(s: float) -> str:
    s = int(max(0, s))
    if s < 60:   return f"{s}s"
    if s < 3600: return f"{s//60}m {s%60:02d}s"
    return f"{s//3600}h {(s%3600)//60:02d}m"

def fmt_speed(bps: float) -> str:
    if bps < 1024:    return f"{bps:.0f} B/s"
    if bps < 1024**2: return f"{bps/1024:.1f} KB/s"
    return f"{bps/1024**2:.1f} MB/s"

async def safe_edit(msg, text: str):
    try:
        await msg.edit_text(text, parse_mode="Markdown")
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after + 1)
        try:
            await msg.edit_text(text, parse_mode="Markdown")
        except Exception:
            pass
    except BadRequest:
        pass
    except Exception:
        pass

def is_owner(update: Update) -> bool:
    if OWNER_ID == 0:
        return True   # Owner ID set nahi toh sab use kar sakte hain
    return update.effective_user.id == OWNER_ID

# ── Download with progress ────────────────────────────────────────────────────

async def download_with_progress(bot, file_id: str, dest: Path,
                                  status_msg, total_size: int):
    import aiohttp
    tg_file = await bot.get_file(file_id)
    url     = tg_file.file_path

    CHUNK      = 512 * 1024
    UPDATE_INT = 2.0
    start      = time.time()
    last_upd   = start
    downloaded = 0

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            if not total_size:
                total_size = int(resp.headers.get("Content-Length", 0))
            with open(dest, "wb") as f:
                async for chunk in resp.content.iter_chunked(CHUNK):
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_upd >= UPDATE_INT:
                        elapsed = now - start
                        speed   = downloaded / elapsed if elapsed else 0
                        pct     = min(downloaded / total_size * 100, 99) if total_size else 0
                        eta     = (total_size - downloaded) / speed if speed and total_size else 0
                        bar     = make_bar(pct)
                        await safe_edit(status_msg,
                            f"⬇️ *Downloading Video*\n\n"
                            f"`[{bar}]` `{pct:.1f}%`\n\n"
                            f"📥 `{fmt_size(downloaded)}`"
                            + (f" / `{fmt_size(total_size)}`" if total_size else "")
                            + f"\n⚡ Speed:   `{fmt_speed(speed)}`\n"
                            f"⏱️ Elapsed: `{fmt_time(elapsed)}`\n"
                            f"⏳ ETA:     `{fmt_time(eta)}`"
                        )
                        last_upd = now

    elapsed = time.time() - start
    return elapsed, downloaded

# ── Get video duration ────────────────────────────────────────────────────────

async def get_duration(path: str) -> float:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        return float(out.decode().strip())
    except Exception:
        return 0.0

# ── Compress with progress ────────────────────────────────────────────────────

async def compress_with_progress(cmd, duration_s, status_msg, res_key, crf, audio_br):
    UPDATE_INT = 3.0
    start      = time.time()
    stderr_buf = []
    cur_time   = 0.0
    fps        = 0.0
    speed      = 0.0
    bitrate    = ""

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    async def read_err():
        nonlocal cur_time, fps, speed, bitrate
        async for raw in proc.stderr:
            line = raw.decode(errors="replace").strip()
            stderr_buf.append(line)
            m = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", line)
            if m:
                h, mi, s = m.groups()
                cur_time = int(h)*3600 + int(mi)*60 + float(s)
            m2 = re.search(r"fps=\s*([\d.]+)", line)
            if m2: fps = float(m2.group(1))
            m3 = re.search(r"speed=\s*([\d.]+)x", line)
            if m3: speed = float(m3.group(1))
            m4 = re.search(r"bitrate=\s*([\d.]+\s*\S+bits/s)", line)
            if m4: bitrate = m4.group(1).strip()

    reader = asyncio.create_task(read_err())

    while proc.returncode is None:
        await asyncio.sleep(UPDATE_INT)
        elapsed = time.time() - start
        pct     = min(cur_time / duration_s * 100, 99) if duration_s else 0
        eta     = (duration_s - cur_time) / speed if speed and duration_s else 0
        bar     = make_bar(pct)
        await safe_edit(status_msg,
            f"⚙️ *Compressing — {res_key}*\n\n"
            f"`[{bar}]` `{pct:.1f}%`\n\n"
            f"🎞️ Processed: `{fmt_time(cur_time)}` / `{fmt_time(duration_s)}`\n"
            f"🔢 FPS:       `{fps:.1f}`\n"
            f"⚡ Speed:    `{speed:.2f}x realtime`\n"
            f"📡 Bitrate:  `{bitrate or '—'}`\n"
            f"⏱️ Elapsed:  `{fmt_time(elapsed)}`\n"
            f"⏳ ETA:      `{fmt_time(eta)}`\n\n"
            f"CRF `{crf}` | Audio `{audio_br}`"
        )

    await reader
    await proc.wait()
    return proc.returncode, "\n".join(stderr_buf), time.time() - start

# ── Upload with progress ──────────────────────────────────────────────────────

async def upload_with_progress(message, output_path, caption, thumb_path,
                                status_msg, res_key, orig_size, comp_size):
    file_size     = output_path.stat().st_size
    start         = time.time()
    done_evt      = asyncio.Event()
    err_holder    = [None]
    ASSUMED_SPEED = 1.2 * 1024 * 1024  # ~1.2 MB/s estimate

    async def do_upload():
        try:
            with open(str(output_path), "rb") as vf:
                if thumb_path and Path(thumb_path).exists():
                    with open(thumb_path, "rb") as tf:
                        await message.reply_video(
                            video=vf, caption=caption,
                            thumbnail=tf, supports_streaming=True,
                            parse_mode="Markdown",
                        )
                else:
                    await message.reply_video(
                        video=vf, caption=caption,
                        supports_streaming=True, parse_mode="Markdown",
                    )
        except Exception as e:
            err_holder[0] = e
        finally:
            done_evt.set()

    asyncio.create_task(do_upload())
    reduction = (orig_size - comp_size) / orig_size * 100 if orig_size else 0

    while not done_evt.is_set():
        await asyncio.sleep(2)
        if done_evt.is_set():
            break
        elapsed   = time.time() - start
        estimated = min(elapsed * ASSUMED_SPEED, file_size * 0.97)
        pct       = min(estimated / file_size * 100, 97) if file_size else 50
        spd       = estimated / elapsed if elapsed else ASSUMED_SPEED
        eta       = (file_size - estimated) / spd if spd else 0
        bar       = make_bar(pct)
        await safe_edit(status_msg,
            f"⬆️ *Uploading — {res_key}*\n\n"
            f"`[{bar}]` `{pct:.1f}%`\n\n"
            f"📦 Size:     `{fmt_size(file_size)}`\n"
            f"⚡ Speed:   `~{fmt_speed(spd)}`\n"
            f"⏱️ Elapsed: `{fmt_time(elapsed)}`\n"
            f"⏳ ETA:     `~{fmt_time(eta)}`\n\n"
            f"📉 Compression saved `{reduction:.1f}%`"
        )

    if err_holder[0]:
        raise err_holder[0]
    return time.time() - start

# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("❌ Aap is bot ko use karne ke authorized nahi hain!")
        return WAITING_VIDEO

    user = update.effective_user
    await update.message.reply_text(
        f"👋 Welcome *{user.first_name}*!\n\n"
        "🎬 *Advanced Video Compressor Bot*\n\n"
        "Real-time progress ke saath:\n"
        "⬇️ Download → ⚙️ Compress → ⬆️ Upload\n\n"
        "📐 *Resolutions:* 144p / 360p / 480p / 720p / 1080p\n"
        "🖼️ Custom Thumbnail | ✏️ Custom Title\n\n"
        "📤 *Video bhejein shuru karne ke liye!*\n\n"
        "/help — Help  |  /cancel — Cancel",
        parse_mode="Markdown",
    )
    return WAITING_VIDEO

# ── /help ─────────────────────────────────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        return
    await update.message.reply_text(
        "📖 *Help Guide*\n\n"
        "*Steps:*\n"
        "1️⃣ Video bhejein (max 2GB)\n"
        "2️⃣ Resolution choose karein\n"
        "3️⃣ Thumbnail bhejein ya skip\n"
        "4️⃣ Title likhein ya skip\n"
        "5️⃣ Real-time progress dekhein!\n\n"
        "*Resolution Guide:*\n"
        "📱 *144p* — Sabse chhota (WhatsApp)\n"
        "📺 *360p* — Normal viewing\n"
        "🖥️ *480p* — SD quality\n"
        "🔵 *720p* — HD *(Recommended)*\n"
        "🟣 *1080p* — Full HD\n\n"
        "*Progress bars dikhate hain:*\n"
        "• Download: actual bytes + speed + ETA\n"
        "• Compress: timestamp + FPS + speed\n"
        "• Upload: size + speed estimate + ETA",
        parse_mode="Markdown",
    )

# ── /cancel ───────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _cleanup(context)
    await update.message.reply_text("❌ Task cancel kar diya!")
    return WAITING_VIDEO

# ── Receive video ─────────────────────────────────────────────────────────────

async def receive_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update):
        await update.message.reply_text("❌ Unauthorized!")
        return WAITING_VIDEO

    message    = update.message
    video      = message.video or message.document
    total_size = getattr(video, "file_size", 0) or 0

    if total_size > 2 * 1024 * 1024 * 1024:
        await message.reply_text("❗ File 2GB se badi hai!")
        return WAITING_VIDEO

    status_msg = await message.reply_text(
        f"⬇️ *Download shuru ho raha hai...*\n\n"
        f"📁 Size: `{fmt_size(total_size)}`\n"
        f"`[░░░░░░░░░░░░░░░░]` `0%`",
        parse_mode="Markdown",
    )

    try:
        user_id = update.effective_user.id
        ext     = ".mp4"
        if message.document:
            fname = getattr(video, "file_name", None) or "video.mp4"
            ext   = Path(fname).suffix or ".mp4"

        dest = DOWNLOAD_DIR / f"{user_id}_{int(time.time())}_input{ext}"

        elapsed, dl_bytes = await download_with_progress(
            context.bot, video.file_id, dest, status_msg, total_size
        )
        speed_avg = dl_bytes / elapsed if elapsed else 0

        await safe_edit(status_msg,
            f"✅ *Download Complete!*\n\n"
            f"`[████████████████]` `100%`\n\n"
            f"📥 Size:      `{fmt_size(dl_bytes)}`\n"
            f"⚡ Avg Speed: `{fmt_speed(speed_avg)}`\n"
            f"⏱️ Time:      `{fmt_time(elapsed)}`"
        )

        context.user_data.update({
            "video_path": str(dest),
            "thumbnail_path": None,
            "title": None,
            "total_size": dl_bytes,
        })

        keyboard = [
            [InlineKeyboardButton("📱 144p  — Sabse chhota",  callback_data="res_144p")],
            [InlineKeyboardButton("📺 360p  — Chhota size",   callback_data="res_360p")],
            [InlineKeyboardButton("🖥️ 480p  — Medium SD",     callback_data="res_480p")],
            [InlineKeyboardButton("🔵 720p  — HD ✅",          callback_data="res_720p")],
            [InlineKeyboardButton("🟣 1080p — Full HD",        callback_data="res_1080p")],
        ]
        await message.reply_text(
            "🎚️ *Resolution select karein:*\n\n"
            "📱 *144p* — Sabse chhota, basic use\n"
            "📺 *360p* — Chhota, acceptable\n"
            "🖥️ *480p* — SD quality\n"
            "🔵 *720p* — HD *(Recommended)*\n"
            "🟣 *1080p* — Full HD, bada size",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return WAITING_QUALITY

    except Exception as e:
        logger.error(f"Download error: {e}", exc_info=True)
        await safe_edit(status_msg, f"❌ Download failed!\n`{e}`")
        return WAITING_VIDEO

# ── Resolution callback ───────────────────────────────────────────────────────

async def resolution_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    res_key = query.data.replace("res_", "")
    preset  = RESOLUTIONS[res_key]
    context.user_data["resolution"] = res_key
    context.user_data["preset"]     = preset

    await query.edit_message_text(
        f"✅ *Resolution set:* `{res_key}` {preset['emoji']} "
        f"— `{preset['w']}×{preset['h']}` | CRF `{preset['crf']}`",
        parse_mode="Markdown",
    )
    kb = [[InlineKeyboardButton("⏭️ Skip Thumbnail", callback_data="skip_thumbnail")]]
    await query.message.reply_text(
        "🖼️ *Thumbnail* (optional)\n\nImage bhejein ya skip karein:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb),
    )
    return WAITING_THUMBNAIL

# ── Thumbnail ─────────────────────────────────────────────────────────────────

async def receive_thumbnail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    if not (message.photo or (
        message.document and message.document.mime_type
        and message.document.mime_type.startswith("image/")
    )):
        await message.reply_text("❗ Photo bhejein ya skip karein!")
        return WAITING_THUMBNAIL

    st = await message.reply_text("⬇️ Thumbnail download ho raha hai...")
    try:
        fobj = message.photo[-1] if message.photo else message.document
        f    = await context.bot.get_file(fobj.file_id)
        path = DOWNLOAD_DIR / f"{update.effective_user.id}_{int(time.time())}_thumb.jpg"
        await f.download_to_drive(str(path))
        context.user_data["thumbnail_path"] = str(path)
        await safe_edit(st, "✅ Thumbnail set!")
    except Exception as e:
        context.user_data["thumbnail_path"] = None
        await safe_edit(st, f"⚠️ Thumbnail skip (error: {e})")

    kb = [[InlineKeyboardButton("⏭️ Skip Title", callback_data="skip_title")]]
    await message.reply_text(
        "✏️ *Title / Caption* (optional)\n\nText likhein ya skip karein:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb),
    )
    return WAITING_TITLE

async def skip_thumbnail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["thumbnail_path"] = None
    await q.edit_message_text("⏭️ Thumbnail skip!")
    kb = [[InlineKeyboardButton("⏭️ Skip Title", callback_data="skip_title")]]
    await q.message.reply_text(
        "✏️ *Title / Caption* (optional)\n\nText likhein ya skip karein:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(kb),
    )
    return WAITING_TITLE

# ── Title ─────────────────────────────────────────────────────────────────────

async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["title"] = update.message.text.strip()
    await update.message.reply_text(
        f"✅ Title: _{context.user_data['title']}_", parse_mode="Markdown"
    )
    return await run_pipeline(update, context)

async def skip_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["title"] = None
    await q.edit_message_text("⏭️ Title skip!")
    return await run_pipeline(q, context)

# ── Main pipeline ─────────────────────────────────────────────────────────────

async def run_pipeline(src, context: ContextTypes.DEFAULT_TYPE):
    message    = src.message
    video_path = context.user_data.get("video_path")
    thumb_path = context.user_data.get("thumbnail_path")
    title      = context.user_data.get("title")
    res_key    = context.user_data.get("resolution", "720p")
    preset     = context.user_data.get("preset", RESOLUTIONS["720p"])
    orig_size  = context.user_data.get("total_size", 0)

    h, w, crf, audio_br = preset["h"], preset["w"], preset["crf"], preset["audio"]

    if not video_path or not Path(video_path).exists():
        await message.reply_text("❌ Video file nahi mili! Dobara bhejein.")
        return WAITING_VIDEO

    user_id     = (src.effective_user.id if hasattr(src, "effective_user")
                   else src.from_user.id)
    output_path = OUTPUT_DIR / f"{user_id}_{int(time.time())}_{res_key}.mp4"

    # Duration
    duration_s = await get_duration(video_path)

    # Compression message
    comp_msg = await message.reply_text(
        f"⚙️ *Compression shuru ho rahi hai...*\n\n"
        f"📐 `{res_key}` ({w}×{h}) | CRF `{crf}`\n"
        f"`[░░░░░░░░░░░░░░░░]` `0%`",
        parse_mode="Markdown",
    )

    scale = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
    )

    if thumb_path and Path(thumb_path).exists():
        cmd = [
            "ffmpeg", "-i", video_path, "-i", thumb_path,
            "-vf", scale, "-c:v", "libx264", "-crf", str(crf),
            "-preset", "ultrafast", "-c:a", "aac", "-b:a", audio_br,
   
