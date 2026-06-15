"""
strategy.py — Strategic seed recommendations for the next scrape session.

Analyzes the current graph to find WHERE coverage is thin, then suggests
targeted seed accounts and hashtags to maximize variance in the next run.

Three signals:
  1. Bridge accounts — sit between clusters, their networks bridge to
     underrepresented communities. Best leads for new diversity.
  2. Outlier accounts — low fit score within their cluster, pull toward
     something else. Often the first trace of a new bubble.
  3. Axis coverage gaps — hand-crafted axes with low spread across clusters
     suggest which hashtag-domains need more accounts.

Writes:
  data/strategy.json — recommended seeds + rationale
  Prints a human-readable report

Usage:
  python3 strategy.py
  Also called by pipeline.py as an optional informational step.
"""

import json
import sqlite3
from collections import Counter
from pathlib import Path

BASE       = Path(__file__).parent
DB_PATH    = BASE / "data" / "bubblescape.db"
GRAPH_JSON = BASE / "data" / "graph.json"
AXIS_JSON  = BASE / "data" / "axis_scores.json"
OUT_PATH   = BASE / "data" / "strategy.json"

MIN_BRIDGE_FOLLOWERS = 500   # only suggest bridges with enough reach
MAX_SEEDS_PER_TYPE   = 8     # cap per category
AXIS_GAP_THRESHOLD   = 0.25  # axis spread below this = coverage gap


def load_graph():
    return json.loads(GRAPH_JSON.read_text())


def load_axis_scores():
    if not AXIS_JSON.exists():
        return {}
    return json.loads(AXIS_JSON.read_text())


def load_db_accounts():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT username, followers, hashtags, fully_collected FROM accounts"
    ).fetchall()
    conn.close()
    return {r["username"]: dict(r) for r in rows}


# ── Signal 1: Bridge accounts ─────────────────────────────────────────────────

def find_bridges(graph, db_accounts):
    """
    Bridge accounts connect multiple clusters. Their following lists
    (if collectible) lead to communities we haven't seen yet.
    Ranked by: number of external cluster connections × follower count.
    """
    bridges = []
    for node in graph["nodes"]:
        if not node.get("is_bridge"):
            continue
        uname = node["id"]
        followers = node.get("followers", 0) or db_accounts.get(uname, {}).get("followers", 0)
        if followers < MIN_BRIDGE_FOLLOWERS:
            continue
        cluster_i = node.get("cluster_index")
        bridges.append({
            "username":    uname,
            "followers":   followers,
            "cluster":     cluster_i,
            "fit_score":   node.get("fit_score", 0),
            "hashtags":    node.get("hashtags", [])[:6],
            "reason":      "bridge — connects multiple clusters",
        })
    bridges.sort(key=lambda x: -x["followers"])
    return bridges[:MAX_SEEDS_PER_TYPE]


# ── Signal 2: Outlier accounts ────────────────────────────────────────────────

def find_outliers(graph, db_accounts):
    """
    Low-fit accounts ended up in a cluster but use few of its hashtags.
    They're often the first signal of a new, not-yet-detected bubble.
    """
    outliers = []
    for node in graph["nodes"]:
        if not node.get("is_outlier"):
            continue
        uname = node["id"]
        followers = node.get("followers", 0) or db_accounts.get(uname, {}).get("followers", 0)
        if followers < MIN_BRIDGE_FOLLOWERS:
            continue
        outliers.append({
            "username":  uname,
            "followers": followers,
            "cluster":   node.get("cluster_index"),
            "fit_score": node.get("fit_score", 0),
            "hashtags":  node.get("hashtags", [])[:6],
            "reason":    f"outlier (fit={node.get('fit_score', 0):.0%}) — may pull toward new bubble",
        })
    outliers.sort(key=lambda x: x["fit_score"])  # lowest fit first
    return outliers[:MAX_SEEDS_PER_TYPE]


# ── Signal 3: Axis coverage gaps ──────────────────────────────────────────────

def find_axis_gaps(axis_scores):
    """
    For each hand-crafted axis, compute the spread (max - min) across clusters.
    Low spread = we don't have accounts that clearly represent this dimension.
    Suggests which hashtag-domains to seed next.
    """
    AXIS_SEEDS = {
        "political_lr": {
            "low":  ["groenlinks", "pvda", "sp", "volt", "links"],
            "high": ["pvv", "fvd", "jfvd", "stemrechts", "wilders"],
        },
        "institutional_trust": {
            "low":  ["nederlandopstand", "complot", "deepstate", "wakeup"],
            "high": ["nos", "nrc", "nieuwsuur", "volkskrant", "rtlnieuws"],
        },
        "religious_identity": {
            "low":  ["seculier", "atheisme", "humanisme"],
            "high": ["islam", "moslim", "ramadan", "quran", "christelijk"],
        },
        "cultural_vs_political": {
            "low":  ["tweedekamer", "politiek", "debat", "stemmen"],
            "high": ["fyp", "grappig", "muziek", "viral", "lifestyle"],
        },
        "reach": {
            "low":  [],  # can't seed for small reach — it's a structural property
            "high": ["nos", "rtl", "ad", "telegraaf"],
        },
    }

    gaps = []
    macro_scores = {cid: scores for cid, scores in axis_scores.items() if len(cid) == 1}
    if not macro_scores:
        return gaps

    for axis_key, seeds in AXIS_SEEDS.items():
        vals = [s.get(axis_key, 0.5) for s in macro_scores.values()]
        if not vals:
            continue
        spread = max(vals) - min(vals)
        if spread < AXIS_GAP_THRESHOLD:
            gaps.append({
                "axis":   axis_key,
                "spread": round(spread, 3),
                "note":   f"Low spread ({spread:.2f}) — few accounts clearly represent this dimension",
                "seed_hashtags_low_end":  seeds.get("low", []),
                "seed_hashtags_high_end": seeds.get("high", []),
            })

    gaps.sort(key=lambda x: x["spread"])
    return gaps


# ── Signal 4: Underrepresented hashtag regions ────────────────────────────────

def find_hashtag_gaps(graph, db_accounts):
    """
    Hashtags that appear in accounts at the cluster boundary (bridges/outliers)
    but are NOT yet in SEED_HASHTAGS. These are organic discovery leads.
    """
    try:
        from config import SEED_HASHTAGS
        seed_set = set(h.lower() for h in SEED_HASHTAGS)
    except ImportError:
        seed_set = set()

    boundary_tags = Counter()
    for node in graph["nodes"]:
        if node.get("is_bridge") or node.get("is_outlier"):
            for tag in node.get("hashtags", []):
                t = tag.lower().strip("#")
                if t and t not in seed_set and len(t) > 2:
                    boundary_tags[t] += 1

    return [
        {"hashtag": tag, "count": count, "reason": "appears in bridge/outlier accounts, not yet seeded"}
        for tag, count in boundary_tags.most_common(12)
        if count >= 2
    ]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("Loading graph...")
    graph = load_graph()
    db_accounts = load_db_accounts()
    axis_scores = load_axis_scores()

    bridges  = find_bridges(graph, db_accounts)
    outliers = find_outliers(graph, db_accounts)
    gaps     = find_axis_gaps(axis_scores)
    hashtag_gaps = find_hashtag_gaps(graph, db_accounts)

    print("\n" + "="*65)
    print("SCRAPE STRATEGY REPORT")
    print("="*65)

    print(f"\n── Bridge accounts ({len(bridges)}) — best leads for new communities ──")
    for b in bridges:
        tags = ", ".join(f"#{t}" for t in b["hashtags"][:4])
        print(f"  @{b['username']:<28} cluster={b['cluster']}  {b['followers']:>7,} followers")
        print(f"    tags: {tags}")

    print(f"\n── Outlier accounts ({len(outliers)}) — may signal new bubble ──")
    for o in outliers:
        tags = ", ".join(f"#{t}" for t in o["hashtags"][:4])
        print(f"  @{o['username']:<28} fit={o['fit_score']:.0%}  cluster={o['cluster']}  {o['followers']:>7,} followers")
        print(f"    tags: {tags}")

    print(f"\n── Axis coverage gaps ({len(gaps)}) — dimensions needing more data ──")
    for g in gaps:
        lo = ", ".join(f"#{t}" for t in g["seed_hashtags_low_end"][:4])
        hi = ", ".join(f"#{t}" for t in g["seed_hashtags_high_end"][:4])
        print(f"  {g['axis']:<25} spread={g['spread']:.2f}  ← add seeds")
        if lo: print(f"    low end:  {lo}")
        if hi: print(f"    high end: {hi}")

    print(f"\n── Unseen hashtags at cluster boundaries ({len(hashtag_gaps)}) ──")
    for h in hashtag_gaps:
        print(f"  #{h['hashtag']:<25} seen in {h['count']} bridge/outlier accounts")

    print("\n" + "="*65)
    print("RECOMMENDED ACTIONS FOR NEXT BOOTSTRAP:")
    print("  1. Add bridge account usernames above to SEED_ACCOUNTS in config.py")
    print("  2. Add hashtags from 'axis gaps' and 'unseen hashtags' to SEED_HASHTAGS")
    print("  3. Run: python3 pipeline.py --bootstrap")
    print("="*65 + "\n")

    output = {
        "generated": __import__("datetime").datetime.utcnow().isoformat(),
        "bridge_accounts":  bridges,
        "outlier_accounts": outliers,
        "axis_gaps":        gaps,
        "hashtag_gaps":     hashtag_gaps,
    }
    OUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"Saved → {OUT_PATH}")
    return output


if __name__ == "__main__":
    main()
