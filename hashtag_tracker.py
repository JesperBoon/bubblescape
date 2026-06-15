"""
hashtag_tracker.py — Learns which hashtags matter and what they signal.

Runs after each collection session. Scans all videos in the DB,
ranks hashtags by frequency and specificity, and groups them into:

  BUBBLE-SPECIFIC   — appears heavily in one cluster, rarely elsewhere
                      → strong bubble signal, use to seed deeper into that bubble
  BRIDGE            — appears across multiple clusters
                      → marks boundary zones, interesting for porosity analysis
  GENERAL-DUTCH     — very common across everything, low signal
                      → safe to deprioritise for collection
  NEW-DISCOVERY     — not in our seed list, appeared organically in the data
                      → most interesting: culture/meme-specific tags we didn't know about

The SATURATION CHECK at the end of the report tells you when to re-bootstrap:
  • Seed return rate  — which seeds are producing results
  • Discovery novelty — what % of this run's NEW-DISCOVERY tags are truly new vs. repeat
  • Verdict           — LOW / MODERATE / HIGH saturation

Usage:
    python hashtag_tracker.py          # print report
    python hashtag_tracker.py --save   # print report + save ranked list to data/hashtags.json
"""

import json
import sys
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

from db import DB_PATH, get_conn
from config import SEED_HASHTAGS, DUTCH_SIGNALS

# Thresholds
MIN_MENTIONS = 3          # ignore tags seen fewer than this many times
BRIDGE_THRESHOLD = 0.3    # tag appears in >30% of accounts = bridge/general
SPECIFIC_THRESHOLD = 0.6  # tag appears in >60% of ONE cluster's accounts = bubble-specific


# ── Data loading ──────────────────────────────────────────────────────────────

def load_hashtag_data() -> dict:
    """
    Returns a dict:
      {
        hashtag: {
          total_mentions: int,
          accounts_using: set of usernames,
          videos: [video_id, ...]
        }
      }
    """
    conn = get_conn()
    videos = conn.execute("SELECT video_id, author, hashtags FROM videos").fetchall()
    conn.close()

    data = defaultdict(lambda: {"total_mentions": 0, "accounts_using": set(), "videos": []})
    for row in videos:
        tags = json.loads(row["hashtags"] or "[]")
        for tag in tags:
            tag = tag.lower().strip().lstrip("#")
            if not tag or len(tag) < 2:
                continue
            data[tag]["total_mentions"] += 1
            data[tag]["accounts_using"].add(row["author"])
            data[tag]["videos"].append(row["video_id"])

    return data


def load_account_count() -> int:
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    conn.close()
    return n


# ── Classification ────────────────────────────────────────────────────────────

def classify_tag(tag: str, accounts_using: set, total_accounts: int) -> str:
    coverage = len(accounts_using) / total_accounts if total_accounts > 0 else 0
    is_seed = tag in SEED_HASHTAGS
    is_dutch_known = any(signal in tag for signal in DUTCH_SIGNALS)

    if coverage > BRIDGE_THRESHOLD:
        return "GENERAL"          # too common to signal anything specific
    elif not is_seed and not is_dutch_known:
        return "NEW-DISCOVERY"    # organically appeared — culture/meme-specific, most interesting
    elif is_seed:
        return "SEED"             # one of our original seeds
    else:
        return "DUTCH-KNOWN"      # matches a Dutch signal but wasn't seeded


def is_dutch_signal(tag: str) -> bool:
    tag_lower = tag.lower()
    return any(signal in tag_lower for signal in DUTCH_SIGNALS)


# ── Saturation check ─────────────────────────────────────────────────────────

def _load_previous_discoveries() -> set:
    """
    Load the NEW-DISCOVERY tags from the last saved hashtags.json.
    Returns an empty set if no previous snapshot exists.
    """
    out_path = DB_PATH.parent / "hashtags.json"
    if not out_path.exists():
        return set()
    try:
        with open(out_path) as f:
            prev = json.load(f)
        return {item["tag"] for item in prev.get("NEW-DISCOVERY", [])}
    except Exception:
        return set()


def _print_saturation_check(classified: dict, filtered: dict, raw_data: dict):
    """
    Prints a saturation block at the end of the report.

    Seed return rate  — which SEED_HASHTAGS are actually producing data in the DB.
    Discovery novelty — what % of this run's NEW-DISCOVERY tags are truly new
                        (not seen in the previous hashtags.json snapshot).
    Verdict           — LOW / MODERATE / HIGH saturation signal.
    """
    print("\n" + "=" * 65)
    print("SATURATION CHECK")
    print("=" * 65)

    # ── 1. Seed return rate ───────────────────────────────────────────────────
    active_seeds   = [s for s in SEED_HASHTAGS if s in filtered]
    inactive_seeds = [s for s in SEED_HASHTAGS if s not in filtered]
    pct_active = len(active_seeds) / len(SEED_HASHTAGS) * 100 if SEED_HASHTAGS else 0

    print(f"\nSeed return rate : {len(active_seeds)}/{len(SEED_HASHTAGS)} seeds "
          f"({pct_active:.0f}%) produced ≥{MIN_MENTIONS} mentions")
    if inactive_seeds:
        print(f"  Inactive seeds : {', '.join('#' + s for s in inactive_seeds[:8])}"
              + (" ..." if len(inactive_seeds) > 8 else ""))
    else:
        print("  All seeds are active — full coverage")

    # ── 2. Discovery novelty ──────────────────────────────────────────────────
    current_discoveries = {item["tag"] for item in classified.get("NEW-DISCOVERY", [])}
    previous_discoveries = _load_previous_discoveries()

    if not previous_discoveries:
        print("\nDiscovery novelty: no previous snapshot — run with --save to start tracking")
    else:
        truly_new = current_discoveries - previous_discoveries
        repeat    = current_discoveries & previous_discoveries
        gone      = previous_discoveries - current_discoveries  # dropped below threshold

        n_total   = len(current_discoveries)
        pct_new   = len(truly_new) / n_total * 100 if n_total else 0
        pct_repeat = len(repeat)   / n_total * 100 if n_total else 0

        print(f"\nDiscovery novelty  : {n_total} NEW-DISCOVERY tags this run")
        print(f"  Truly new        : {len(truly_new):>3} ({pct_new:.0f}%) — "
              "genuine new signal from the frontier")
        print(f"  Already seen     : {len(repeat):>3} ({pct_repeat:.0f}%) — "
              "repeat from last run")
        if gone:
            print(f"  Dropped out      : {len(gone):>3} — fell below {MIN_MENTIONS} mention threshold")

        # Verdict
        if pct_new >= 50:
            verdict = "LOW  — still discovering new territory, keep collecting"
            verdict_sym = "🟢"
        elif pct_new >= 20:
            verdict = "MODERATE — mix of new and repeat, frontier is narrowing"
            verdict_sym = "🟡"
        else:
            verdict = "HIGH — mostly repeating old discoveries, time to re-bootstrap with new seeds"
            verdict_sym = "🔴"

        print(f"\nSaturation signal  : {verdict_sym}  {verdict}")

        # Show the top truly-new tags this run
        if truly_new:
            truly_new_items = [
                item for item in classified.get("NEW-DISCOVERY", [])
                if item["tag"] in truly_new
            ]
            truly_new_items.sort(key=lambda x: x["unique_accounts"], reverse=True)
            print(f"\nTop new discoveries this run:")
            for item in truly_new_items[:8]:
                dutch_flag = " ★" if item["dutch_signal"] else ""
                print(f"  #{item['tag']:<28} "
                      f"{item['mentions']:>4} mentions  "
                      f"{item['unique_accounts']:>3} accounts{dutch_flag}")

    print("=" * 65)


# ── Report ────────────────────────────────────────────────────────────────────

def generate_report(save: bool = False):
    if not DB_PATH.exists():
        print("No database found. Run a collection session first.")
        return

    print("Loading hashtag data from database...")
    data = load_hashtag_data()
    total_accounts = load_account_count()

    if not data:
        print("No video data yet. Run python session.py --bootstrap first.")
        return

    # Filter to meaningful tags only
    filtered = {
        tag: info for tag, info in data.items()
        if info["total_mentions"] >= MIN_MENTIONS
    }

    print(f"\nFound {len(filtered)} hashtags with ≥{MIN_MENTIONS} mentions (from {len(data)} total)\n")

    # Classify
    classified = defaultdict(list)
    for tag, info in filtered.items():
        category = classify_tag(tag, info["accounts_using"], total_accounts)
        classified[category].append({
            "tag": tag,
            "mentions": info["total_mentions"],
            "unique_accounts": len(info["accounts_using"]),
            "coverage_pct": round(len(info["accounts_using"]) / total_accounts * 100, 1) if total_accounts else 0,
            "dutch_signal": is_dutch_signal(tag),
        })

    # Sort each group by mentions
    for cat in classified:
        classified[cat].sort(key=lambda x: x["mentions"], reverse=True)

    # ── Print ──────────────────────────────────────────────────────────────────
    print("=" * 65)
    print("HASHTAG TRACKER REPORT")
    print(f"Total accounts in DB: {total_accounts}")
    print("=" * 65)

    sections = [
        ("NEW-DISCOVERY", "🔍 NEW DISCOVERIES — appeared organically, not in seed list"),
        ("DUTCH-KNOWN",   "🇳🇱 DUTCH-KNOWN — matches Dutch signals but wasn't seeded"),
        ("SEED",          "🌱 SEEDS — your original seed hashtags"),
        ("GENERAL",       "🌐 GENERAL — too common to signal much"),
    ]

    suggested_new_seeds = []

    for cat, label in sections:
        items = classified.get(cat, [])
        if not items:
            continue
        print(f"\n{label} ({len(items)} tags)")
        print("-" * 65)
        for item in items[:20]:  # show top 20 per category
            bar = "█" * min(20, int(item["mentions"] / max(1, filtered[items[0]["tag"]]["total_mentions"]) * 20))
            dutch_flag = " ★" if item["dutch_signal"] else ""
            print(
                f"  #{item['tag']:<30} "
                f"{item['mentions']:>4} mentions  "
                f"{item['unique_accounts']:>3} accounts{dutch_flag}"
            )
            # Flag high-value new discoveries with Dutch signal as seed candidates
            if cat == "NEW-DISCOVERY" and item["unique_accounts"] >= 5 and item["dutch_signal"]:
                suggested_new_seeds.append(item["tag"])

    # ── Suggestions ───────────────────────────────────────────────────────────
    if suggested_new_seeds:
        print("\n" + "=" * 65)
        print("💡 SUGGESTED NEW SEED HASHTAGS")
        print("These appeared organically and look politically relevant.")
        print("Consider adding them to SEED_HASHTAGS in config.py:")
        print()
        for tag in suggested_new_seeds[:10]:
            print(f'    "{tag}",')

    # ── Saturation check ──────────────────────────────────────────────────────
    _print_saturation_check(classified, filtered, data)

    print("\n" + "=" * 65)
    print("HOW TO USE THIS REPORT")
    print("  • NEW-DISCOVERY tags are most valuable — culture/meme-specific")
    print("  • Add promising ones to SEED_HASHTAGS in config.py")
    print("  • GENERAL tags = low signal, don't prioritise them")
    print("  • Run after every collection session to track what's emerging")
    print("  • SATURATION HIGH → time to re-bootstrap with updated seeds")
    print("=" * 65 + "\n")

    # ── Save ──────────────────────────────────────────────────────────────────
    if save:
        out_path = DB_PATH.parent / "hashtags.json"
        output = {}
        for cat, items in classified.items():
            output[cat] = items
        with open(out_path, "w") as f:
            json.dump(output, f, indent=2)
        print(f"Saved to {out_path}")


if __name__ == "__main__":
    save = "--save" in sys.argv
    generate_report(save=save)
