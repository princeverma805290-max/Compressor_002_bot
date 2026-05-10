#!/bin/bash
set -e

echo "=== FFmpeg install ==="
apt-get update -qq && apt-get install -y ffmpeg
ffmpeg -version | head -1

echo "=== Force remove old telegram lib ==="
pip uninstall python-telegram-bot -y 2>/dev/null || true

echo "=== Install exact versions ==="
pip install --no-cache-dir \
    "python-telegram-bot==20.7" \
    "aiohttp==3.9.5"

echo "=== Verify version ==="
python3 -c "import telegram; print('PTB version:', telegram.__version__)"

echo "=== Build complete! ==="
