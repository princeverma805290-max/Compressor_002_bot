#!/bin/bash
set -e

echo "=== FFmpeg install ==="
apt-get update -qq && apt-get install -y ffmpeg
ffmpeg -version | head -1

echo "=== Remove old libraries ==="
pip uninstall python-telegram-bot telegram -y 2>/dev/null || true

echo "=== Install packages ==="
pip install --no-cache-dir pyTelegramBotAPI==4.20.0 telethon==1.36.0 requests==2.31.0

echo "=== Verify ==="
python3 -c "import telebot; print('telebot OK')"
python3 -c "import telethon; print('telethon OK')"

echo "=== Build complete! ==="
