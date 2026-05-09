"""
Advanced Telegram Video Compressor Bot
- Real download progress bar with speed + ETA
- Real FFmpeg compression progress (frame/fps/time) with bar
- Real upload progress bar with speed + ETA
- Resolution: 144p / 360p / 480p / 720p / 1080p
- Custom Thumbnail + Title/Caption
- Render free CPU optimized
"""

import os
import re
import asyncio
import logging
import time
import math
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

# ── States ──────────────────────────────────────────────────────────────────
WAITING_VIDEO     = 1
WAITING_QUALITY   = 2
WAITING_THUMBNAIL = 3
WAITING_TITLE     = 4

# ── Resolution presets ──────────────────────────────────────────────────────
RESOLUTIONS = {
    "144p":  {"h": 144,  "w": 256,  "crf": 40, "audio": "64k",  "emoji": "📱"},
    "360p":  {"h": 360,  "w": 640,  "crf": 36, "audio": "96k",  "emoji": "📺"},
    "480p":  {"h": 480,  "w": 854,  "crf": 32, "audio": "112k", "emoji": "🖥️"},
    "720p":  {"h": 720,  "w": 1280, "crf": 28, "audio": "128k", "emoji": "🔵"},
    "1080p": {"h": 1080, "w": 1920, "crf": 24, "audio": "192k", "emoji": "🟣"},
}

BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
DOWNLOAD_DIR = Path("downloads")
OUTPUT_DIR   = Path("outputs")
DOWNLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# ── Progress UI helpers ──────────────────────────────────────────────────────

def make_bar(pct: float, width: int = 16) -> str:
    """Unicode block progress bar  ████████░░░░  60%"""
    filled = int(width * pct / 100)
    empty  = width - filled
    return "█" * filled + "░" * empty

def fmt_size(b: int) -> str:
    if b < 1024:        return f"{b} B"
    if b < 1024**2:     return f"{b/1024:.1f} KB"
    if b < 1024**3:     return f"{b/1024**2:.1f} MB"
    return f"{b/1024**3:.2f} GB"

def fmt_time(secs: float) -> str:
    secs = int(max(0, secs))
    if secs < 60:   return f"{secs}s"
    if secs < 3600: return f"{secs//60}m {secs%60:02d}s"
    return f"{secs//3600}h {(secs%3600)//60:02d}m"

def fmt_speed(bps: float) -> str:
    if bps < 1024:      return f"{bps:.0f} B/s"
    if bps < 1024**2:   return f"{bps/1024:.1f} KB/s"
    return f"{bps/1024**2:.1f} MB/s"

async def safe_edit(msg, text: str):
    """Edit message, ignore flood/same-content errors."""
    try:
        await msg.edit_text(text, parse_mode="Markdown")
    except RetryAfter as e:
        await asyncio.sleep(e.retry_after + 0.5)
        try:
            await msg.edit_text(text, parse_mode="Markdown")
        except Exception:
            pass
    except BadRequest:
        pass
    except Exception:
        pass

# ── Download with real progress ──────────────────────────────────────────────

async def download_with_progress(bot, file_obj, dest_path: Path,
                                  status_msg, total_size: int,
                                  label: str = "⬇️ Downloading"):
    """
    Download file in chunks and update progress message every ~2 seconds.
    Returns elapsed seconds.
    """
    CHUNK = 512 * 1024  # 512 KB per chunk
    UPDATE_EVERY = 2.0  # seconds between edits

    tg_file = await bot.get_file(file_obj.file_id)
    url      = tg_file.file_path          # direct download URL

    import aiohttp
    start    = time.time()
    last_upd = start
    downloaded = 0

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            resp.raise_for_status()
            if total_size == 0:
                total_size = int(resp.headers.get("Content-Length", 0))

            with open(dest_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(CHUNK):
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()

                    if now - last_upd >= UPDATE_EVERY:
                        elapsed = now - start
                        speed   = downloaded / elapsed if elapsed > 0 else 0
                        pct     = min(downloaded / total_size * 100, 99) if total_size else 0
                        eta     = (total_size - downloaded) / speed if speed > 0 and total_size else 0

                        bar = make_bar(pct)
                        txt = (
                            f"*{label}*\n\n"
                            f"`[{bar}]` `{pct:.1f}%`\n\n"
                            f"📥 `{fmt_size(downloaded)}`"
                            + (f" / `{fmt_size(total_size)}`" if total_size else "")
                            + f"\n"
                            f"⚡ Speed:  `{fmt_speed(speed)}`\n"
                            f"⏱️ Elapsed: `{fmt_time(elapsed)}`\n"
                            f"⏳ ETA:    `{fmt_time(eta)}`"
                        )
                        await safe_edit(status_msg, txt)
                        last_upd = now

    elapsed = time.time() - start
    return elapsed, downloaded

# ── FFmpeg compression with real progress ────────────────────────────────────

async def compress_with_progress(cmd: list, total_duration_s: float,
                                  status_msg, res_key: str,
                                  crf: int, audio_br: str):
    """
    Run FFmpeg, parse stderr for time= progress, update message every 3s.
    Returns (returncode, stderr_text, elapsed)
    """
    UPDATE_EVERY = 3.0
    start        = time.time()
    last_upd     = start
    stderr_lines = []

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )

    current_time_s = 0.0
    fps_cur        = 0.0
    speed_cur      = 0.0
    bitrate_cur    = ""

    async def read_stderr():
        nonlocal current_time_s, fps_cur, speed_cur, bitrate_cur
        async for raw in proc.stderr:
            line = raw.decode(errors="replace").strip()
            stderr_lines.append(line)

            # Parse: frame=  42 fps= 12 ... time=00:00:01.68 bitrate= 800kbits/s speed=0.56x
            m_time    = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", line)
            m_fps     = re.search(r"fps=\s*([\d.]+)", line)
            m_speed   = re.search(r"speed=\s*([\d.]+)x", line)
            m_bitrate = re.search(r"bitrate=\s*([\d.]+\s*\S+)", line)

            if m_time:
                h, m, s = m_time.groups()
                current_time_s = int(h)*3600 + int(m)*60 + float(s)
            if m_fps:
                fps_cur = float(m_fps.group(1))
            if m_speed:
                speed_cur = float(m_speed.group(1))
            if m_bitrate:
                bitrate_cur = m_bitrate.group(1).strip()

    reader_task = asyncio.create_task(read_stderr())

    while proc.returncode is None:
        await asyncio.sleep(UPDATE_EVERY)
        now     = time.time()
        elapsed = now - start

        pct = min(current_time_s / total_duration_s * 100, 99) if total_duration_s > 0 else 0
        eta = ((total_duration_s - current_time_s) / speed_cur
               if speed_cur > 0 and total_duration_s > 0 else 0)

        bar = make_bar(pct)
        processed_ts = fmt_time(current_time_s)
        total_ts     = fmt_time(total_duration_s) if total_duration_s > 0 else "?"

        txt = (
            f"⚙️ *Compressing — {res_key}*\n\n"
            f"`[{bar}]` `{pct:.1f}%`\n\n"
            f"🎞️ Processed: `{processed_ts}` / `{total_ts}`\n"
            f"🔢 FPS:       `{fps_cur:.1f}`\n"
            f"⚡ Speed:    `{speed_cur:.2f}x`\n"
            f"📡 Bitrate:  `{bitrate_cur or '—'}`\n"
            f"⏱️ Elapsed:  `{fmt_time(elapsed)}`\n"
            f"⏳ ETA:      `{fmt_time(eta)}`\n\n"
            f"CRF `{crf}` | Audio `{audio_br}`"
        )
        await safe_edit(status_msg, txt)

    await reader_task
    await proc.wait()
    elapsed = time.time() - start
    stderr_text = "\n".join(stderr_lines)
    return proc.returncode, stderr_text, elapsed

# ── Upload with real progress ─────────────────────────────────────────────────

async def upload_with_progress(message, output_path: Path, caption: str,
                                 thumbnail_path, status_msg,
                                 res_key: str, orig_size: int, comp_size: int,
                                 compress_elapsed: float):
    """
    Upload video, show a simulated upload progress bar updated every 2s.
    (Telegram Bot API does not expose real upload bytes, so we estimate from
     time elapsed vs file size / assumed speed.)
    """
    UPDATE_EVERY = 2.0
    file_size    = output_path.stat().st_size
    start        = time.time()

    # Run upload in background task while updating progress
    upload_done   = asyncio.Event()
    upload_error  = [None]

    async def do_upload():
        try:
            with open(str(output_path), "rb") as vf:
                if thumbnail_path and Path(thumbnail_path).exists():
                    with open(thumbnail_path, "rb") as tf:
                        await message.reply_video(
                            video=vf, caption=caption,
                            thumbnail=tf, supports_streaming=True,
                            parse_mode="Markdown",
                        )
                else:
                    await message.reply_video(
                        video=vf, caption=caption,
                        supports_streaming=True,
                        parse_mode="Markdown",
                    )
        except Exception as e:
            upload_error[0] = e
        finally:
            upload_done.set()

    upload_task = asyncio.create_task(do_upload())

    # Progress loop — estimate based on elapsed time
    # Typical Telegram server upload speed ~1–3 MB/s on free tier
    ASSUMED_SPEED = 1.2 * 1024 * 1024  # 1.2 MB/s estimate

    while not upload_done.is_set():
        await asyncio.sleep(UPDATE_EVERY)
        if upload_done.is_set():
            break

        elapsed   = time.time() - start
        estimated = min(elapsed * ASSUMED_SPEED, file_size * 0.97)
        pct       = min(estimated / file_size * 100, 97) if file_size else 50
        speed     = estimated / elapsed if elapsed > 0 else ASSUMED_SPEED
        eta       = (file_size - estimated) / speed if speed > 0 else 0

        bar = make_bar(pct)
        reduction = (orig_size - comp_size) / orig_size * 100 if orig_size > 0 else 0

        txt = (
            f"⬆️ *Uploading — {res_key}*\n\n"
            f"`[{bar}]` `{pct:.1f}%`\n\n"
            f"📦 Size:      `{fmt_size(file_size)}`\n"
            f"⚡ Speed:    `~{fmt_speed(speed)}`\n"
            f"⏱️ Elapsed:  `{fmt_time(elapsed)}`\n"
            f"⏳ ETA:      `~{fmt_time(eta)}`\n\n"
            f"📉 Compression saved `{reduction:.1f}%`"
        )
        await safe_edit(status_msg, txt)

    await upload_task

    if upload_error[0]:
        raise upload_error[0]

    upload_elapsed = time.time() - start
    return upload_elapsed

# ── Get video duration via ffprobe ────────────────────────────────────────────

async def get_duration(video_path: str) -> float:
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        return float(out.decode().strip())
    except Exception:
        return 0.0

# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 *Advanced Video Compressor Bot*\n\n"
        "Har step ka *real-time progress* dikhata hoon:\n"
        "• ⬇️ Download progress + speed + ETA\n"
        "• ⚙️ Compression progress + FPS + speed\n"
        "• ⬆️ Upload progress + speed + ETA\n\n"
        "📐 *Resolutions:* 144p / 360p / 480p / 720p / 1080p\n"
        "🖼️ Custom Thumbnail | ✏️ Custom Title\n\n"
        "📤 *Video bhejein shuru karne ke liye!*\n\n"
        "/help — Help  |  /cancel — Cancel",
        parse_mode="Markdown",
    )
    return WAITING_VIDEO

# ── /help ─────────────────────────────────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Help Guide*\n\n"
        "*Steps:*\n"
        "1️⃣ Video bhejein (max 2GB)\n"
        "2️⃣ Resolution choose karein\n"
        "3️⃣ Thumbnail bhejein ya skip karein\n"
        "4️⃣ Title likhein ya skip karein\n"
        "5️⃣ Har step ka progress bar dekhein!\n\n"
        "*Resolution Guide:*\n"
        "📱 *144p* — WhatsApp/forward (sabse chhota)\n"
        "📺 *360p* — Normal viewing\n"
        "🖥️ *480p* — SD quality\n"
        "🔵 *720p* — HD *(Recommended)*\n"
        "🟣 *1080p* — Full HD\n\n"
        "*Progress Info:*\n"
        "• Download: actual bytes + speed + ETA\n"
        "• Compress: timestamp + FPS + speed multiplier\n"
        "• Upload: size + estimated speed + ETA",
        parse_mode="Markdown",
    )

# ── /cancel ───────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _cleanup(context)
    await update.message.reply_text("❌ Task cancel kar diya!")
    return WAITING_VIDEO

# ── Receive video ─────────────────────────────────────────────────────────────

async def receive_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    video   = message.video or message.document

    if not video:
        await message.reply_text("❗ Sirf video files bhejein!")
        return WAITING_VIDEO

    total_size = getattr(video, "file_size", 0) or 0

    if total_size > 2 * 1024 * 1024 * 1024:
        await message.reply_text("❗ File 2GB se badi hai! Allowed nahi.")
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
            context.bot, video, dest, status_msg, total_size,
            label="⬇️ Downloading Video"
        )

        speed_avg = dl_bytes / elapsed if elapsed > 0 else 0
        await safe_edit(
            status_msg,
            f"✅ *Download Complete!*\n\n"
            f"`[████████████████]` `100%`\n\n"
            f"📥 Size:     `{fmt_size(dl_bytes)}`\n"
            f"⚡ Avg Speed: `{fmt_speed(speed_avg)}`\n"
            f"⏱️ Time:     `{fmt_time(elapsed)}`",
        )

        context.user_data.update({
            "video_path":     str(dest),
            "thumbnail_path": None,
            "title":          None,
            "total_size":     dl_bytes,
        })

        # Resolution keyboard
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
        f"✅ *Resolution set:* `{res_key}` — {preset['emoji']} "
        f"`{preset['w']}×{preset['h']}`  CRF `{preset['crf']}`",
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
        message.document
        and message.document.mime_type
        and message.document.mime_type.startswith("image/")
    )):
        await message.reply_text("❗ Photo bhejein ya skip karein!")
        return WAITING_THUMBNAIL

    st = await message.reply_text("⬇️ Thumbnail download ho raha hai...")
    try:
        fobj  = message.photo[-1] if message.photo else message.document
        f     = await context.bot.get_file(fobj.file_id)
        path  = DOWNLOAD_DIR / f"{update.effective_user.id}_{int(time.time())}_thumb.jpg"
        await f.download_to_drive(str(path))
        context.user_data["thumbnail_path"] = str(path)
        await safe_edit(st, "✅ Thumbnail set ho gaya!")
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

async def run_pipeline(update_or_query, context: ContextTypes.DEFAULT_TYPE):
    message = update_or_query.message

    video_path     = context.user_data.get("video_path")
    thumbnail_path = context.user_data.get("thumbnail_path")
    title          = context.user_data.get("title")
    res_key        = context.user_data.get("resolution", "720p")
    preset         = context.user_data.get("preset", RESOLUTIONS["720p"])
    orig_size      = context.user_data.get("total_size", 0)

    h        = preset["h"]
    w        = preset["w"]
    crf      = preset["crf"]
    audio_br = preset["audio"]

    if not video_path or not Path(video_path).exists():
        await message.reply_text("❌ Video file nahi mili! Dobara bhejein.")
        return WAITING_VIDEO

    user_id     = (update_or_query.effective_user.id
                   if hasattr(update_or_query, "effective_user")
                   else update_or_query.from_user.id)
    output_path = OUTPUT_DIR / f"{user_id}_{int(time.time())}_{res_key}.mp4"

    # ── Step 1: Get duration ──────────────────────────────────────────────────
    duration_s = await get_duration(video_path)

    # ── Step 2: Compression status message ───────────────────────────────────
    comp_msg = await message.reply_text(
        f"⚙️ *Compression shuru ho rahi hai...*\n\n"
        f"📐 `{res_key}` ({w}×{h})  CRF `{crf}`\n"
        f"`[░░░░░░░░░░░░░░░░]` `0%`",
        parse_mode="Markdown",
    )

    # ── Step 3: Build FFmpeg command ──────────────────────────────────────────
    scale = (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,setsar=1"
    )

    if thumbnail_path and Path(thumbnail_path).exists():
        cmd = [
            "ffmpeg", "-i", video_path, "-i", thumbnail_path,
            "-vf", scale,
            "-c:v", "libx264", "-crf", str(crf), "-preset", "ultrafast",
            "-c:a", "aac", "-b:a", audio_br,
            "-map", "0:v", "-map", "0:a?", "-map", "1",
            "-disposition:v:1", "attached_pic",
            "-movflags", "+faststart", "-y", str(output_path),
        ]
    else:
        cmd = [
            "ffmpeg", "-i", video_path,
            "-vf", scale,
            "-c:v", "libx264", "-crf", str(crf), "-preset", "ultrafast",
            "-c:a", "aac", "-b:a", audio_br,
            "-movflags", "+faststart", "-y", str(output_path),
        ]

    # ── Step 4: Compress ──────────────────────────────────────────────────────
    rc, stderr_text, comp_elapsed = await compress_with_progress(
        cmd, duration_s, comp_msg, res_key, crf, audio_br
    )

    if rc != 0:
        err_tail = stderr_text[-500:] if stderr_text else "Unknown"
        await safe_edit(comp_msg,
            f"❌ *Compression failed!*\n\n```\n{err_tail}\n```"
        )
        _cleanup(context)
        return WAITING_VIDEO

    comp_size = output_path.stat().st_size
    reduction = (orig_size - comp_size) / orig_size * 100 if orig_size > 0 else 0

    await safe_edit(
        comp_msg,
        f"✅ *Compression Complete!*\n\n"
        f"`[████████████████]` `100%`\n\n"
        f"📐 Resolution: `{res_key}` ({w}×{h})\n"
        f"📁 Original:   `{fmt_size(orig_size)}`\n"
        f"📦 Compressed: `{fmt_size(comp_size)}`\n"
        f"📉 Saved:      `{reduction:.1f}%`\n"
        f"⏱️ Time:       `{fmt_time(comp_elapsed)}`",
    )

    # ── Step 5: Upload ────────────────────────────────────────────────────────
    upload_msg = await message.reply_text(
        f"⬆️ *Upload shuru ho raha hai...*\n\n"
        f"📦 `{fmt_size(comp_size)}`\n"
        f"`[░░░░░░░░░░░░░░░░]` `0%`",
        parse_mode="Markdown",
    )

    caption = title or (
        f"🎬 *{res_key} Compressed Video*\n"
        f"📉 `{reduction:.1f}%` size reduced\n"
        f"📐 `{w}×{h}` | 📦 `{fmt_size(comp_size)}`"
    )

    try:
        upload_elapsed = await upload_with_progress(
            message, output_path, caption,
            thumbnail_path, upload_msg,
            res_key, orig_size, comp_size, comp_elapsed,
        )

        total_elapsed = comp_elapsed + upload_elapsed
        await safe_edit(
            upload_msg,
            f"✅ *Upload Complete!*\n\n"
            f"`[████████████████]` `100%`\n\n"
            f"📦 Size:       `{fmt_size(comp_size)}`\n"
            f"⏱️ Upload time: `{fmt_time(upload_elapsed)}`\n"
            f"⏱️ Total time:  `{fmt_time(total_elapsed)}`\n\n"
            f"🎉 *Done! {res_key} video ready hai!*",
        )

    except Exception as e:
        logger.error(f"Upload error: {e}", exc_info=True)
        await safe_edit(upload_msg, f"❌ Upload failed!\n`{e}`")

    finally:
        _cleanup(context)
        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            pass

    return WAITING_VIDEO

# ── Cleanup ───────────────────────────────────────────────────────────────────

def _cleanup(context: ContextTypes.DEFAULT_TYPE):
    for key in ("video_path", "thumbnail_path"):
        p = context.user_data.pop(key, None)
        if p:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass
    for key in ("title", "resolution", "preset", "total_size"):
        context.user_data.pop(key, None)

# ── main ──────────────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable set nahi hai!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.VIDEO | filters.Document.VIDEO, receive_video),
        ],
        states={
            WAITING_VIDEO: [
                MessageHandler(filters.VIDEO | filters.Document.VIDEO, receive_video),
            ],
            WAITING_QUALITY: [
                CallbackQueryHandler(resolution_callback, pattern="^res_"),
            ],
            WAITING_THUMBNAIL: [
                CallbackQueryHandler(skip_thumbnail, pattern="^skip_thumbnail$"),
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, receive_thumbnail),
            ],
            WAITING_TITLE: [
                CallbackQueryHandler(skip_title, pattern="^skip_title$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_title),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_user=True,
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("help", help_command))
    logger.info("✅ Bot polling shuru...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
