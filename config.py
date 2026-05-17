"""Gramag Knowledge Graph — Centralized configuration."""

import os
from dotenv import load_dotenv

load_dotenv()

# Azure OpenAI
AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_OPENAI_CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "gramag-chat")
AZURE_OPENAI_VISION_DEPLOYMENT = os.getenv("AZURE_OPENAI_VISION_DEPLOYMENT", "gramag-vision")
AZURE_OPENAI_EMBED_DEPLOYMENT = os.getenv("AZURE_OPENAI_EMBED_DEPLOYMENT", "gramag-embed")
EMBED_DIMENSIONS = int(os.getenv("EMBED_DIMENSIONS", 3072))

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
