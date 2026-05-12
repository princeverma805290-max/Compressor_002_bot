#!/bin/bash
set -e

echo "=== FFmpeg install ==="
apt-get update -qq && apt-get install -y ffmpeg
ffmpeg -version | head -1

echo "=== Remove old telegram libraries ==="
pip uninstall python-telegram-bot telegram -y 2>/dev/null || true

echo "=== Install packages ==="
pip install --no-cache-dir pyTelegramBotAPI==4.20.0 requests==2.31.0

echo "=== Verify ==="
python3 -c "import telebot; print('pyTelegramBotAPI installed OK')"

echo "=== Build complete! ==="
