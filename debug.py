"""
debug.py — Print raw API responses so we can fix the parser.

Usage:
    python3 debug.py

Costs 3 credits. Shows the actual JSON structure for:
  1. A user profile
  2. A user's videos (first page)
  3. A user's following (first page)
  4. A hashtag's videos (first page)
"""

import json
import os
from dotenv import load_dotenv
import requests

load_dotenv()
API_KEY = os.getenv("KONBINI_API_KEY")
BASE = "https://api.konbiniapi.com/v1"
HEADERS = {"Authorization": f"Bearer {API_KEY}"}

def fetch(path, params=None):
    r = requests.get(f"{BASE}{path}", headers=HEADERS, params=params or {})
    print(f"\n{'='*60}")
    print(f"GET {path}  →  {r.status_code}")
    print(f"Credits used: {r.headers.get('X-Credits-Used')}  Remaining: {r.headers.get('X-Credits-Remaining')}")
    print(f"{'='*60}")
    print(json.dumps(r.json(), indent=2)[:3000])  # first 3000 chars
    return r.json()

# 1. Profile
fetch("/tiktok/users/example_user")

# 2. Their videos
fetch("/tiktok/users/example_user/videos")

# 3. Their following
fetch("/tiktok/users/example_user/following")

# 4. A hashtag
fetch("/tiktok/tags/nederlandspolitiek/videos")
