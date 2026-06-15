"""
axis_scorer.py — compute semantic axis scores per cluster for Bubblescape axis view.

Reads:
  bubbletree/bubbletree_data.json  → cluster/account membership
  data/bubblescape.db               → per-account hashtag lists
  data/graph.json                   → density, size, reach

Writes:
  data/axis_scores.json
"""

import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path

BASE = Path(__file__).parent
BUBBLETREE = BASE / "bubbletree" / "bubbletree_data.json"
GRAPH_JSON  = BASE / "data" / "graph.json"
DB_PATH     = BASE / "data" / "bubblescape.db"
OUT_PATH    = BASE / "data" / "axis_scores.json"

# ── axis signal hashtags ────────────────────────────────────────────────────

POLITICAL_RIGHT = {
    "fvd", "pvv", "wilders", "rechts", "migratie", "nederlandopstand",
    "forumvoordemocratie", "stemrechts", "geertwilders", "stemfvd",
    "pvvnederland", "partijvoordedemocratie",
}

POLITICAL_LEFT = {
    "groenlinks", "d66", "klimaat", "palestina", "stemlinks",
    "denk", "pvda", "sp", "partijvandearbeid", "bnnvara",
    "links", "klimaatcrisis", "solidariteit",
}

HIGH_TRUST_INSTITUTIONS = {
    "nos", "nrc", "nieuwsuur", "bnnvara", "nporadio1", "omroep",
    "rtlnieuws", "volkskrant", "trouw", "nrclive",
}

LOW_TRUST_INSTITUTIONS = {
    "fvd", "nederlandopstand", "complot", "wakeup", "deepstate",
    "mainstream", "staatsomroep", "msm", "fakenews",
}

RELIGIOUS_TAGS = {
    "islam", "moslim", "ramadan", "islamicreminder", "quran",
    "islamitisch", "vasten", "allah", "islamtiktok", "moslimnl",
    "islamicquotes", "deen", "islamiclife",
}

LIFESTYLE_TAGS = {
    "fyp", "viral", "grappig", "muziek", "school", "feest",
    "humor", "fun", "trending", "comedy", "entertainment",
    "relatable", "herkenbaar", "dagelijks",
}

POLITICAL_TAGS = {
    "politiek", "tweedekamer", "verkiezingen", "kabinet", "coalitie",
    "europa", "democratie", "pvv", "d66", "groenlinks", "vvd",
    "fvd", "bbb", "nsc", "overheidspolitiek",
}


# ── helpers ─────────────────────────────────────────────────────────────────

def load_account_hashtags(db_path: Path) -> dict[str, list[str]]:
    """Return {username: [hashtag, ...]} for all fully-collected accounts."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT username, hashtags FROM accounts WHERE hashtags IS NOT NULL")
    result = {}
    for username, raw in c.fetchall():
        try:
            tags = json.loads(raw) if isinstance(raw, str) else raw
            result[username] = [t.lower().strip("#") for t in tags if t]
        except Exception:
            pass
    conn.close()
    return result


def collect_cluster_hashtags(accounts: list[str], account_tags: dict) -> list[str]:
    """Flat list of all hashtags used by accounts in this cluster."""
    all_tags = []
    for username in accounts:
        all_tags.extend(account_tags.get(username, []))
    return all_tags


def axis_political_lr(tags: list[str]) -> float:
    """Left-right score: -1 = far left, +1 = far right, 0 = neutral."""
    right = sum(1 for t in tags if t in POLITICAL_RIGHT)
    left  = sum(1 for t in tags if t in POLITICAL_LEFT)
    total = right + left
    if total == 0:
        return 0.0
    return (right - left) / total


def axis_institutional_trust(tags: list[str]) -> float:
    """High = trusts institutions, Low = distrusts. Range -1 to +1."""
    high = sum(1 for t in tags if t in HIGH_TRUST_INSTITUTIONS)
    low  = sum(1 for t in tags if t in LOW_TRUST_INSTITUTIONS)
    total = high + low
    if total == 0:
        return 0.0
    return (high - low) / total


def axis_religious_identity(tags: list[str]) -> float:
    """Fraction of tags that are religious-identity signals. Range 0-1."""
    if not tags:
        return 0.0
    religious = sum(1 for t in tags if t in RELIGIOUS_TAGS)
    return religious / len(tags)


def axis_cultural_vs_political(tags: list[str]) -> float:
    """Ratio: +1 = purely lifestyle/cultural, -1 = purely political."""
    lifestyle = sum(1 for t in tags if t in LIFESTYLE_TAGS)
    political = sum(1 for t in tags if t in POLITICAL_TAGS)
    total = lifestyle + political
    if total == 0:
        return 0.0
    return (lifestyle - political) / total


def normalize_scores(clusters: dict) -> dict:
    """Min-max normalize each axis to 0-1 range across all clusters."""
    axes = ["political_lr", "institutional_trust", "religious_identity",
            "cultural_vs_political", "reach"]

    for axis in axes:
        values = [v[axis] for v in clusters.values()]
        mn, mx = min(values), max(values)
        span = mx - mn
        for cid in clusters:
            raw = clusters[cid][axis]
            clusters[cid][f"{axis}_raw"] = raw
            clusters[cid][axis] = (raw - mn) / span if span > 0 else 0.5

    # bubble_closure is already 0-1 (density), just copy to _raw
    for cid in clusters:
        clusters[cid]["bubble_closure_raw"] = clusters[cid]["bubble_closure"]

    return clusters


# ── main ────────────────────────────────────────────────────────────────────

def main():
    bubbletree = json.loads(BUBBLETREE.read_text())
    graph      = json.loads(GRAPH_JSON.read_text())
    account_tags = load_account_hashtags(DB_PATH)

    # Build account → cluster mapping from bubbletree
    # cluster_accounts[cid] = direct accounts at that level only
    cluster_accounts: dict[str, list[str]] = {}
    # parent_map[child_cid] = parent_cid for roll-up
    parent_map: dict[str, str] = {}
    # cluster hierarchy order (BFS-order for roll-up)
    cluster_order: list[str] = []

    def walk(node, parent_id=None):
        cid = node.get("id") or parent_id
        if cid:
            if cid not in cluster_accounts:
                cluster_accounts[cid] = []
                cluster_order.append(cid)
            if parent_id and cid != parent_id:
                parent_map[cid] = parent_id
            for acc in node.get("accounts", []):
                uname = acc.get("username") or acc.get("name")
                if uname and uname not in cluster_accounts[cid]:
                    cluster_accounts[cid].append(uname)
        for sub in node.get("subclusters", []):
            walk(sub, parent_id=cid)

    for cluster in bubbletree["clusters"]:
        walk(cluster)

    # Roll-up: propagate accounts from children to parents (bottom-up)
    for cid in reversed(cluster_order):
        parent = parent_map.get(cid)
        if parent:
            for uname in cluster_accounts[cid]:
                if uname not in cluster_accounts[parent]:
                    cluster_accounts[parent].append(uname)

    # Build reach lookup from graph.json
    reach_lookup: dict[str, float] = {}
    size_lookup:  dict[str, int]   = {}
    density_lookup: dict[str, float] = {}

    def walk_graph(node, cid=None):
        node_id = str(node.get("cluster_index", "")) or cid
        if "sub_label" in node:
            node_id = node["sub_label"].replace("·", "-")

        reach_lookup[node_id]   = node.get("total_followers", 0) or node.get("reach", 0)
        size_lookup[node_id]    = node.get("size", 0)
        density_lookup[node_id] = node.get("internal_density") or node.get("density", 0)

        for sub in node.get("subclusters", []):
            walk_graph(sub, cid=node_id)

    for c in graph["clusters"]:
        walk_graph(c)

    # Also pull reach from bubbletree directly for sub-clusters
    def walk_bubbletree_reach(node):
        cid = node.get("id")
        if cid:
            reach_lookup[cid]   = node.get("reach", reach_lookup.get(cid, 0))
            size_lookup[cid]    = node.get("size", size_lookup.get(cid, 0))
            density_lookup[cid] = node.get("density", density_lookup.get(cid, 0))
        for sub in node.get("subclusters", []):
            walk_bubbletree_reach(sub)

    for c in bubbletree["clusters"]:
        walk_bubbletree_reach(c)

    # Build per-account followers from DB for sub-cluster reach
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username, followers FROM accounts")
    followers_lookup = {row[0]: row[1] or 0 for row in c.fetchall()}
    conn.close()

    # Compute scores
    results = {}
    for cid, accounts in cluster_accounts.items():
        tags = collect_cluster_hashtags(accounts, account_tags)
        tag_count = len(tags)

        reach = reach_lookup.get(cid, 0)
        if not reach and accounts:
            reach = sum(followers_lookup.get(u, 0) for u in accounts)
        size  = size_lookup.get(cid, len(accounts))

        results[cid] = {
            "cluster_id":            cid,
            "account_count":         len(accounts),
            "tag_count":             tag_count,
            "political_lr":          axis_political_lr(tags),
            "institutional_trust":   axis_institutional_trust(tags),
            "religious_identity":    axis_religious_identity(tags),
            "bubble_closure":        density_lookup.get(cid, 0.0),
            "cultural_vs_political": axis_cultural_vs_political(tags),
            "reach":                 math.log1p(reach) if reach > 0 else 0.0,
            "reach_raw_followers":   reach,
        }

    results = normalize_scores(results)

    OUT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"Wrote {len(results)} cluster scores to {OUT_PATH}")

    # Pretty-print summary
    AXES = ["political_lr", "institutional_trust", "religious_identity",
            "bubble_closure", "cultural_vs_political", "reach"]
    header = f"{'Cluster':<8}" + "".join(f"{a[:8]:>10}" for a in AXES)
    print()
    print(header)
    print("-" * len(header))
    for cid in sorted(results, key=lambda x: (len(x), x)):
        row = results[cid]
        scores = "".join(f"{row[a]:>10.3f}" for a in AXES)
        print(f"{cid:<8}{scores}  (n={row['account_count']})")


if __name__ == "__main__":
    main()
