# 🎬 Telegram Video Compressor Bot

FFmpeg se video compress karne wala Telegram bot - Render pe free deploy!

## Features
- ✅ Video compression (FFmpeg libx264)
- ✅ Quality select: High / Medium / Low / Custom CRF
- ✅ Custom Thumbnail set karna
- ✅ Custom Title/Caption
- ✅ Render free tier compatible
- ✅ Fake HTTP server (Render ke liye)

---

## 📦 Files

```
├── main.py          # Entry point (fake server + bot)
├── bot.py           # Telegram bot logic
├── fake_server.py   # HTTP server (Render keep-alive)
├── requirements.txt # Python packages
├── render.yaml      # Render config
└── build.sh         # FFmpeg install script
```

---

## 🚀 Render Pe Deploy Kaise Karein

### Step 1: GitHub pe push karein
```bash
git init
git add .
git commit -m "Initial commit"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

### Step 2: Render pe jayen
1. [render.com](https://render.com) pe account banayein
2. "New +" → "Web Service" click karein
3. GitHub repo connect karein

### Step 3: Settings configure karein
| Setting | Value |
|---------|-------|
| **Runtime** | Python 3 |
| **Build Command** | `bash build.sh` |
| **Start Command** | `python main.py` |
| **Plan** | Free |

### Step 4: Environment Variable add karein
- Key: `BOT_TOKEN`
- Value: `Apna bot token yahan dalein`

### Step 5: Deploy!
"Create Web Service" click karein - ho gaya! 🎉

---

## 🤖 Bot Token Kaise Milega

1. Telegram pe [@BotFather](https://t.me/BotFather) pe jayen
2. `/newbot` command bhejein
3. Naam aur username dalein
4. Token copy karein → Render environment variable mein dalein

---

## 💬 Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Bot shuru karein |
| `/help` | Help dekhein |
| `/cancel` | Current task cancel karein |

## 📊 Quality Guide

| Quality | CRF | Use Case |
|---------|-----|----------|
| High | 23 | Best quality |
| Medium | 28 | Balanced (recommended) |
| Low | 35 | Small file size |
| Custom | 0-51 | Apni marzi |

---

## ⚠️ Free Tier Limitations
- 512 MB RAM
- Shared CPU
- 750 hours/month
- Bot sleep ho sakta hai idle pe (15 min)
- Ping karte rahein ya UptimeRobot use karein

## 🔧 Local Test Kaise Karein

```bash
# Install
pip install -r requirements.txt
sudo apt install ffmpeg  # Linux
# brew install ffmpeg    # Mac

# Chalayein
export BOT_TOKEN="aapka_token_yahan"
python main.py
```
