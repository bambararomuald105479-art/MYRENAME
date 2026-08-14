import os
from dotenv import load_dotenv

load_dotenv()

# ============ TELEGRAM ============
# TOUS LES 3 SONT OBLIGATOIRES pour un bot avec fichiers > 50MB!
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH")

# Validation
if not BOT_TOKEN or BOT_TOKEN == "YOUR_BOT_TOKEN":
    raise ValueError("❌ BOT_TOKEN manquant!")
if API_ID == 0 or not API_HASH:
    raise ValueError("❌ API_ID ou API_HASH manquant!")

# ============ FFMPEG MICRO ============
FFMPEG_MICRO_API_KEYS = [
    os.getenv("FFMPEG_MICRO_API_KEY_1", ""),
    os.getenv("FFMPEG_MICRO_API_KEY_2", ""),
    os.getenv("FFMPEG_MICRO_API_KEY_3", ""),
    os.getenv("FFMPEG_MICRO_API_KEY_4", ""),
    os.getenv("FFMPEG_MICRO_API_KEY_5", ""),
]
FFMPEG_MICRO_API_KEYS = [k for k in FFMPEG_MICRO_API_KEYS if k]

if not FFMPEG_MICRO_API_KEYS:
    raise ValueError("❌ Au moins une clé FFmpeg Micro requise!")

# ============ SÉCURITÉ ============
# IDs des 3 utilisateurs autorisés
ALLOWED_USER_IDS = []
allowed_str = os.getenv("ALLOWED_USER_IDS", "")
if allowed_str:
    ALLOWED_USER_IDS = [int(uid.strip()) for uid in allowed_str.split(",")]
