#!/bin/bash
# Render build script - FFmpeg install karta hai

echo "=== FFmpeg install ho raha hai ==="
apt-get update -qq && apt-get install -y ffmpeg

echo "=== Python packages install ho rahe hain ==="
pip install -r requirements.txt

echo "=== Build complete! ==="
ffmpeg -version | head -1
