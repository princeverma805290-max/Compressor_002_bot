"""
Fake HTTP server - Render ke liye zaroori hai
Render ko lagta hai ke ek web service chal rahi hai
"""
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import os
import json
from datetime import datetime

PORT = int(os.environ.get("PORT", 10000))


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health" or self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            data = {
                "status": "ok",
                "message": "Telegram Compressor Bot is running!",
                "timestamp": datetime.now().isoformat(),
                "service": "video-compressor-bot"
            }
            self.wfile.write(json.dumps(data).encode())
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")
    
    def log_message(self, format, *args):
        pass  # Logs suppress karo


def start_fake_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"[Fake Server] Port {PORT} pe chal raha hai (Render keep-alive)")
    server.serve_forever()


def run_in_background():
    thread = threading.Thread(target=start_fake_server, daemon=True)
    thread.start()
    return thread
