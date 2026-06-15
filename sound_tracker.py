"""
sound_tracker.py — Tracks which audio is spreading across accounts.

Runs after each collection session. Original sounds (user-created) are
more interesting than licensed music — a political speech remix, protest
chant, or community meme audio is a strong bubble signal.

Usage:
    python3 sound_tracker.py          # print report
    python3 sound_tracker.py --save   # print + save to data/sounds.json
"""

import json
import sys
from collections import defaultdict

from db import DB_PATH, get_conn

MIN_ACCOUNTS = 3  # ignore sounds used by fewer accounts than this

# Markers that indicate a user-created original sound
ORIGINAL_MARKERS = [
    "original sound", "son original", "geluid van", "origineel geluid",
    "оригинальный звук", "звук", "صوت", "صدای اصلی"
]


def is_original(title: str) -> bool:
    t = title.lower()
    return any(m in t for m in ORIGINAL_MARKERS)


def load_sound_data() -> dict:
    """Load audio from fully-collected accounts only — clean signal."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT v.audio_id, v.audio_title, v.author
        FROM videos v
        JOIN accounts a ON v.author = a.username
        WHERE a.fully_collected = 1
          AND v.audio_id  != ''
          AND v.audio_title != ''
    """).fetchall()
    conn.close()

    data = defaultdict(lambda: {"title": "", "accounts": set(), "uses": 0})
    for row in rows:
        aid = row["audio_id"]
        data[aid]["title"]  = row["audio_title"]
        data[aid]["accounts"].add(row["author"])
        data[aid]["uses"]  += 1

    return data


def generate_report(save: bool = False):
    if not DB_PATH.exists():
        print("No database found. Run a collection session first.")
        return

    print("Loading sound data...")
    data = load_sound_data()
    total_sounds = len(data)

    shared = {
        aid: info for aid, info in data.items()
        if len(info["accounts"]) >= MIN_ACCOUNTS
    }

    if not shared:
        print(f"\nNo sounds shared by {MIN_ACCOUNTS}+ accounts yet "
              f"({total_sounds:,} unique sounds in DB — keep collecting).")
        return

    originals = {aid: v for aid, v in shared.items() if is_original(v["title"])}
    licensed  = {aid: v for aid, v in shared.items() if not is_original(v["title"])}

    def by_accounts(d):
        return sorted(d.items(), key=lambda x: len(x[1]["accounts"]), reverse=True)

    print(f"\nUnique sounds in DB       : {total_sounds:,}")
    print(f"Shared by {MIN_ACCOUNTS}+ accounts   : {len(shared)}"
          f" ({len(originals)} original, {len(licensed)} licensed)\n")

    print("=" * 65)
    print("SOUND TRACKER REPORT")
    print("=" * 65)

    print(f"\n🎙️  ORIGINAL SOUNDS — user-created, culturally specific ({len(originals)})")
    print("-" * 65)
    if originals:
        for aid, info in by_accounts(originals)[:20]:
            print(f"  {len(info['accounts']):>3} accounts | "
                  f"{info['uses']:>4} uses | {info['title'][:50]}")
    else:
        print("  None yet — keep collecting.")

    print(f"\n🎵  LICENSED / NAMED MUSIC ({len(licensed)})")
    print("-" * 65)
    if licensed:
        for aid, info in by_accounts(licensed)[:20]:
            print(f"  {len(info['accounts']):>3} accounts | "
                  f"{info['uses']:>4} uses | {info['title'][:50]}")
    else:
        print("  None yet.")

    print("\n" + "=" * 65)
    print("WHAT TO WATCH FOR")
    print("  • Original sound in 5+ accounts  = potential bubble signal")
    print("  • Same sound across opposite bubbles = bridge sound")
    print("  • Sound used 10x by same few accounts = community anthem")
    print("=" * 65 + "\n")

    if save:
        out = {"original": [], "licensed": []}
        for aid, info in by_accounts(originals):
            out["original"].append({
                "audio_id": aid, "title": info["title"],
                "accounts": list(info["accounts"]), "uses": info["uses"]
            })
        for aid, info in by_accounts(licensed):
            out["licensed"].append({
                "audio_id": aid, "title": info["title"],
                "accounts": list(info["accounts"]), "uses": info["uses"]
            })
        path = DB_PATH.parent / "sounds.json"
        with open(path, "w") as f:
            json.dump(out, f, indent=2)
        print(f"Saved to {path}")


if __name__ == "__main__":
    generate_report(save="--save" in sys.argv)
