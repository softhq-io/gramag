"""Gramag Knowledge Graph — Centralized configuration."""

import os
from dotenv import load_dotenv

load_dotenv()

# Gemini
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
EMBED_MODEL = "gemini-embedding-001"
EMBED_DIMENSIONS = 3072
CHAT_MODEL = "gemini-3-pro-preview"
EXTRACTION_MODEL = "gemini-2.0-flash"  # Fast model for PDF structured extraction

# FalkorDB
FALKORDB_HOST = os.getenv("FALKORDB_HOST", "localhost")
FALKORDB_PORT = int(os.getenv("FALKORDB_PORT", 6379))
FALKORDB_GRAPH = os.getenv("FALKORDB_GRAPH", "gramag")

# Data paths
DATA_DIR = os.getenv("DATA_DIR", "/Users/piotrzwolinski/projects/gramag/data/Gramag Daten")
ERP_DIR = os.path.join(DATA_DIR, "ERP")
PDF_DIR = os.path.join(DATA_DIR, "Servicedaten")
INDEX_DIR = os.getenv("INDEX_DIR", "/Users/piotrzwolinski/projects/gramag/index")

# JWT Auth
JWT_SECRET = os.getenv("JWT_SECRET", "gramag-dev-secret-change-in-prod")
JWT_ACCESS_EXPIRE_HOURS = int(os.getenv("JWT_ACCESS_EXPIRE_HOURS", 8))
JWT_REFRESH_EXPIRE_DAYS = int(os.getenv("JWT_REFRESH_EXPIRE_DAYS", 7))

# Noise articles (shipping, travel, service hours — not real spare parts)
NOISE_KEYWORDS = [
    "porto", "verpackung", "spedition", "camion", "luftpolster",
    "technikerstunden", "fahrzeit", "google maps", "kulanz",
    "pauschale verpflegung", "mittagessen", "nachtessen", "hotel",
    "reisespesen", "parkplatz", "maut", "holzbox",
    "dummy", "wegpauschale", "pauschale kleinmaterial",
    "pauschale km", "tagespauschale",
]
