"""
Main entry point
Fake server + Bot dono ek saath chalata hai
"""
import os
import sys
import logging

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Fake server start karo (Render ke liye)
from fake_server import run_in_background
run_in_background()
logger.info("✅ Fake web server start ho gaya")

# Bot start karo
from bot import main
logger.info("✅ Telegram bot start ho raha hai...")
main()

