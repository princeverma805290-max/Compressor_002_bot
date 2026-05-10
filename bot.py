import os
import logging
import asyncio
import time
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, filters, ContextTypes
)

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# States
WAITING_VIDEO = 1
WAITING_QUALITY = 2
WAITING_THUMBNAIL = 3
WAITING_TITLE = 4

# Resolution presets — height: (width, crf, audio_bitrate, label)
RESOLUTION_PRESETS = {
    "144p":  {"height": 144,  "width": 256,  "crf": 40, "audio": "64k",  "label": "📱 144p  — Sabse chhota size"},
    "360p":  {"height": 360,  "width": 640,  "crf": 36, "audio": "96k",  "label": "📺 360p  — Chhota size"},
    "480p":  {"height": 480,  "width": 854,  "crf": 32, "audio": "112k", "label": "🖥️ 480p  — Medium quality"},
    "720p":  {"height": 720,  "width": 1280, "crf": 28, "audio": "128k", "label": "🔵 720p  — HD (Recommended)"},
    "1080p": {"height": 1080, "width": 1920, "crf": 24, "audio": "192k", "label": "🟣 1080p — Full HD, bada size"},
}

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
DOWNLOAD_DIR = Path("downloads")
OUTPUT_DIR = Path("outputs")
DOWNLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)


# ─────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 *Video Compressor Bot*\n\n"
        "Main aapke videos ko compress karta hoon!\n\n"
        "📌 *Features:*\n"
        "• Resolution select: 144p / 360p / 480p / 720p / 1080p\n"
        "• Custom Thumbnail set karein\n"
        "• Custom Title / Caption add karein\n"
        "• Fast FFmpeg compression\n\n"
        "🚀 *Shuru karne ke liye video bhejein!*\n\n"
        "/help — Help\n"
        "/cancel — Cancel",
        parse_mode="Markdown"
    )
    return WAITING_VIDEO


# ─────────────────────────────────────────────
# /help
# ─────────────────────────────────────────────
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *Help Guide*\n\n"
        "1️⃣ Video bhejein bot ko\n"
        "2️⃣ Resolution select karein (144p – 1080p)\n"
        "3️⃣ Thumbnail bhejein (optional)\n"
        "4️⃣ Title likhein (optional)\n"
        "5️⃣ Compressed video milega!\n\n"
        "⚠️ *Note:* Max 2GB video support hai\n\n"
        "*Resolution Guide:*\n"
        "📱 144p — WhatsApp forward ke liye\n"
        "📺 360p — Normal viewing\n"
        "🖥️ 480p — SD quality\n"
        "🔵 720p — HD (best balance)\n"
        "🟣 1080p — Full HD",
        parse_mode="Markdown"
    )


# ─────────────────────────────────────────────
# /cancel
# ─────────────────────────────────────────────
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _cleanup_user_files(context)
    await update.message.reply_text("❌ Task cancel kar diya gaya!")
    return WAITING_VIDEO


# ─────────────────────────────────────────────
# Video receive
# ─────────────────────────────────────────────
async def receive_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message
    video = message.video or message.document

    if not video:
        await message.reply_text("❗ Koi video nahi mila. Sirf video files bhejein!")
        return WAITING_VIDEO

    if video.file_size and video.file_size > 2 * 1024 * 1024 * 1024:
        await message.reply_text("❗ File bohot badi hai! Max 2GB allowed hai.")
        return WAITING_VIDEO

    status_msg = await message.reply_text("⬇️ Video download ho raha hai...")

    try:
        file = await context.bot.get_file(video.file_id)
        user_id = update.effective_user.id

        if message.video:
            ext = ".mp4"
        else:
            fname = getattr(video, 'file_name', None) or "video.mp4"
            ext = Path(fname).suffix or ".mp4"

        video_path = DOWNLOAD_DIR / f"{user_id}_{int(time.time())}_input{ext}"
        await file.download_to_drive(str(video_path))

        context.user_data['video_path'] = str(video_path)
        context.user_data['thumbnail_path'] = None
        context.user_data['title'] = None

        file_size_str = format_size(video.file_size) if video.file_size else "Unknown"
        await status_msg.edit_text(f"✅ Video download ho gaya! ({file_size_str})")

        # Show resolution buttons
        keyboard = [
            [InlineKeyboardButton("📱 144p  — Sabse chhota",  callback_data="res_144p")],
            [InlineKeyboardButton("📺 360p  — Chhota size",   callback_data="res_360p")],
            [InlineKeyboardButton("🖥️ 480p  — Medium",        callback_data="res_480p")],
            [InlineKeyboardButton("🔵 720p  — HD ✅",          callback_data="res_720p")],
            [InlineKeyboardButton("🟣 1080p — Full HD",        callback_data="res_1080p")],
        ]

        await message.reply_text(
            "🎚️ *Resolution select karein:*\n\n"
            "📱 *144p* — Sabse chhota file, basic use\n"
            "📺 *360p* — Chhota size, acceptable quality\n"
            "🖥️ *480p* — SD quality, balanced\n"
            "🔵 *720p* — HD quality *(Recommended)*\n"
            "🟣 *1080p* — Full HD, best quality\n\n"
            "⚠️ Original se badi resolution select nahi hogi",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return WAITING_QUALITY

    except Exception as e:
        logger.error(f"Download error: {e}")
        await status_msg.edit_text(f"❌ Download failed: {str(e)}")
        return WAITING_VIDEO


# ─────────────────────────────────────────────
# Resolution callback
# ─────────────────────────────────────────────
async def resolution_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    res_key = query.data.replace("res_", "")          # e.g. "720p"
    preset = RESOLUTION_PRESETS[res_key]

    context.user_data['resolution'] = res_key
    context.user_data['res_preset'] = preset

    await query.edit_message_text(
        f"✅ Resolution set: *{res_key}* — {preset['label']}",
        parse_mode="Markdown"
    )

    keyboard = [[InlineKeyboardButton("⏭️ Skip", callback_data="skip_thumbnail")]]
    await query.message.reply_text(
        "🖼️ *Thumbnail bhejein* (optional)\n\n"
        "Compressed video ke liye thumbnail image bhejein\n"
        "Ya skip karein:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return WAITING_THUMBNAIL


# ─────────────────────────────────────────────
# Thumbnail receive
# ─────────────────────────────────────────────
async def receive_thumbnail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message

    if not (message.photo or (message.document and message.document.mime_type and
                               message.document.mime_type.startswith("image/"))):
        await message.reply_text("❗ Image nahi mila. Photo bhejein ya skip karein!")
        return WAITING_THUMBNAIL

    status_msg = await message.reply_text("⬇️ Thumbnail download ho raha hai...")

    try:
        file_obj = message.photo[-1] if message.photo else message.document
        file = await context.bot.get_file(file_obj.file_id)
        user_id = update.effective_user.id
        thumb_path = DOWNLOAD_DIR / f"{user_id}_{int(time.time())}_thumb.jpg"
        await file.download_to_drive(str(thumb_path))
        context.user_data['thumbnail_path'] = str(thumb_path)
        await status_msg.edit_text("✅ Thumbnail set ho gaya!")
    except Exception as e:
        await status_msg.edit_text(f"⚠️ Thumbnail failed, skip kar raha hoon: {e}")
        context.user_data['thumbnail_path'] = None

    keyboard = [[InlineKeyboardButton("⏭️ Skip", callback_data="skip_title")]]
    await message.reply_text(
        "✏️ *Title / Caption likhein* (optional)\n\n"
        "Compressed video ke saath bheja jayega\n"
        "Ya skip karein:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return WAITING_TITLE


async def skip_thumbnail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['thumbnail_path'] = None
    await query.edit_message_text("⏭️ Thumbnail skip kar diya!")

    keyboard = [[InlineKeyboardButton("⏭️ Skip", callback_data="skip_title")]]
    await query.message.reply_text(
        "✏️ *Title / Caption likhein* (optional)\n\n"
        "Compressed video ke saath bheja jayega\n"
        "Ya skip karein:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return WAITING_TITLE


# ─────────────────────────────────────────────
# Title receive
# ─────────────────────────────────────────────
async def receive_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['title'] = update.message.text.strip()
    await update.message.reply_text(f"✅ Title set: _{context.user_data['title']}_",
                                    parse_mode="Markdown")
    return await start_compression(update, context)


async def skip_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['title'] = None
    await query.edit_message_text("⏭️ Title skip kar diya!")
    return await start_compression(query, context)


# ─────────────────────────────────────────────
# Compression
# ─────────────────────────────────────────────
async def start_compression(update_or_query, context: ContextTypes.DEFAULT_TYPE):
    message = update_or_query.message

    video_path     = context.user_data.get('video_path')
    thumbnail_path = context.user_data.get('thumbnail_path')
    title          = context.user_data.get('title')
    res_key        = context.user_data.get('resolution', '720p')
    preset         = context.user_data.get('res_preset', RESOLUTION_PRESETS['720p'])

    height      = preset['height']
    width       = preset['width']
    crf         = preset['crf']
    audio_br    = preset['audio']

    if not video_path or not Path(video_path).exists():
        await message.reply_text("❌ Video file nahi mili! Dobara bhejein.")
        return WAITING_VIDEO

    user_id = (update_or_query.effective_user.id
               if hasattr(update_or_query, 'effective_user')
               else update_or_query.from_user.id)

    status_msg = await message.reply_text(
        f"🔄 *Compression shuru ho rahi hai...*\n\n"
        f"📐 Resolution: *{res_key}* ({width}×{height})\n"
        f"⚙️ CRF: {crf} | Audio: {audio_br}\n"
        f"⏳ Thoda wait karein...",
        parse_mode="Markdown"
    )

    output_path = OUTPUT_DIR / f"{user_id}_{int(time.time())}_{res_key}.mp4"

    try:
        # scale filter — keep aspect ratio, pad if needed, force even dimensions
        scale_filter = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"setsar=1"
        )

        # Base FFmpeg command
        cmd = [
            "ffmpeg", "-i", video_path,
            "-vf", scale_filter,
            "-c:v", "libx264",
            "-crf", str(crf),
            "-preset", "ultrafast",   # Render free CPU ke liye
            "-c:a", "aac",
            "-b:a", audio_br,
            "-movflags", "+faststart",
            "-y",
        ]

        # Thumbnail embed
        if thumbnail_path and Path(thumbnail_path).exists():
            cmd = [
                "ffmpeg",
                "-i", video_path,
                "-i", thumbnail_path,
                "-vf", scale_filter,
                "-c:v", "libx264",
                "-crf", str(crf),
                "-preset", "ultrafast",
                "-c:a", "aac",
                "-b:a", audio_br,
                "-map", "0:v",
                "-map", "0:a?",
                "-map", "1",
                "-disposition:v:1", "attached_pic",
                "-movflags", "+faststart",
                "-y",
            ]

        cmd.append(str(output_path))

        start_time = time.time()
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        elapsed = time.time() - start_time

        if process.returncode != 0:
            err = stderr.decode(errors='replace')[-600:] if stderr else "Unknown"
            await status_msg.edit_text(
                f"❌ *Compression failed!*\n\n```{err}```",
                parse_mode="Markdown"
            )
            return WAITING_VIDEO

        original_size   = Path(video_path).stat().st_size
        compressed_size = output_path.stat().st_size
        reduction = ((original_size - compressed_size) / original_size * 100
                     if original_size > 0 else 0)

        await status_msg.edit_text(
            f"✅ *Compression complete!*\n\n"
            f"📐 Resolution: *{res_key}* ({width}×{height})\n"
            f"📁 Original:   {format_size(original_size)}\n"
            f"📦 Compressed: {format_size(compressed_size)}\n"
            f"📉 Reduction:  {reduction:.1f}%\n"
            f"⏱️ Time:       {elapsed:.1f}s\n\n"
            f"⬆️ Upload ho raha hai...",
            parse_mode="Markdown"
        )

        caption = title or (
            f"🎬 *{res_key} Compressed Video*\n"
            f"📉 Size reduced by {reduction:.1f}%\n"
            f"📐 {width}×{height}"
        )

        with open(str(output_path), 'rb') as vf:
            if thumbnail_path and Path(thumbnail_path).exists():
                with open(thumbnail_path, 'rb') as tf:
                    await message.reply_video(
                        video=vf, caption=caption,
                        thumbnail=tf, supports_streaming=True,
                        parse_mode="Markdown"
                    )
            else:
                await message.reply_video(
                    video=vf, caption=caption,
                    supports_streaming=True, parse_mode="Markdown"
                )

        await status_msg.edit_text(
            f"✅ *Done! {res_key} video ready hai!* 🎉",
            parse_mode="Markdown"
        )

    except Exception as e:
        logger.error(f"Compression error: {e}")
        await status_msg.edit_text(f"❌ Error: {str(e)}")

    finally:
        _cleanup_user_files(context)
        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            pass

    return WAITING_VIDEO


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def _cleanup_user_files(context: ContextTypes.DEFAULT_TYPE):
    for key in ('video_path', 'thumbnail_path'):
        path = context.user_data.pop(key, None)
        if path:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass
    for key in ('title', 'resolution', 'res_preset'):
        context.user_data.pop(key, None)


def format_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes/1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes/1024**2:.1f} MB"
    else:
        return f"{size_bytes/1024**3:.2f} GB"


# ─────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────
def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN environment variable nahi mila!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    conv_handler = ConversationHandler(
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

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("help", help_command))

    logger.info("✅ Bot start ho raha hai...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

