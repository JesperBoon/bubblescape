"""
bubbletree_export.py
Reads ../data/bubblescape.db + ../data/graph.json → bubbletree_data.json
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
DATA = HERE / ".." / "data"
DB_PATH = DATA / "bubblescape.db"
GRAPH_PATH = DATA / "graph.json"
NAMES_PATH = DATA / "cluster_names.json"
OUT_PATH = HERE / "bubbletree_data.json"

MACRO_COLORS = {
    1: "#1D9E75",
    2: "#5B4FCF",
    3: "#D97B2C",
    4: "#C94040",
    5: "#4A7FC1",
}


def load_cluster_names() -> dict:
    """Load cluster_names.json — keyed by hashtag fingerprint."""
    if not NAMES_PATH.exists():
        return {"macro": {}, "sub": {}}
    return json.loads(NAMES_PATH.read_text(encoding="utf-8"))


def _fingerprint_key(tags: list) -> str:
    return ",".join(sorted(tags[:5]))


def load_accounts():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT username, display_name, followers, bio, hashtags FROM accounts"
    ).fetchall()
    conn.close()
    result = {}
    for r in rows:
        tags = []
        if r["hashtags"]:
            try:
                tags = json.loads(r["hashtags"])
            except Exception:
                pass
        result[r["username"]] = {
            "display_name": r["display_name"] or "",
            "followers": r["followers"] or 0,
            "bio": r["bio"] or "",
            "hashtags": tags,
        }
    return result


def load_graph():
    with open(GRAPH_PATH) as f:
        return json.load(f)


def build_account_card(node, db_accounts):
    username = node["id"]
    db = db_accounts.get(username, {})
    hashtags = node.get("hashtags") or db.get("hashtags") or []
    return {
        "username": username,
        "display_name": db.get("display_name", ""),
        "followers": db.get("followers", node.get("followers", 0)),
        "bio": (db.get("bio") or "")[:120],
        "fit_score": round(node.get("fit_score", 0.5), 3),
        "is_bridge": bool(node.get("is_bridge")),
        "is_outlier": bool(node.get("is_outlier")),
        "top_hashtags": hashtags[:8],
    }


def build_subcluster(sc, nodes_by_sub, db_accounts, depth=1):
    sub_label = sc["sub_label"]
    accounts_in = sorted(
        [build_account_card(n, db_accounts) for n in nodes_by_sub.get(sub_label, [])],
        key=lambda x: -x["fit_score"],
    )

    child_subs = []
    for child in sc.get("subclusters") or []:
        child_subs.append(build_subcluster(child, nodes_by_sub, db_accounts, depth + 1))

    top_hashtags = sc.get("distinctive_hashtags") or []

    return {
        "id": sub_label,
        "name": sc.get("name") or sc.get("label_hint") or sub_label,
        "description": sc.get("description"),
        "depth": sc.get("depth", depth),
        "size": sc.get("size", len(accounts_in)),
        "density": round(sc.get("density", 0), 3),
        "confidence": round(sc.get("confidence", 0), 3),
        "top_hashtags": top_hashtags[:6],
        "accounts": accounts_in,
        "subclusters": child_subs,
    }


def build_nodes_by_sub_label(nodes):
    """Map every sub_cluster_label → list of nodes (all depths)."""
    mapping = {}
    for n in nodes:
        label = n.get("sub_cluster_label") or ""
        # Strip the deepest level to match sub-label at each depth:
        # e.g. "1a·1·2" → keys: "1a", "1a·1", "1a·1·2"
        parts = label.split("·")
        for i in range(len(parts)):
            key = "·".join(parts[: i + 1])
            mapping.setdefault(key, []).append(n)
    return mapping


def main():
    print("Loading graph.json …")
    graph = load_graph()
    print("Loading accounts from DB …")
    db_accounts = load_accounts()
    cluster_names = load_cluster_names()

    nodes = graph["nodes"]
    clusters_raw = graph["clusters"]

    # Index nodes by sub_cluster_label for deep lookups
    nodes_by_sub = build_nodes_by_sub_label(nodes)

    # Also index by cluster_index for macro-level accounts
    nodes_by_cluster = {}
    for n in nodes:
        ci = n.get("cluster_index", 0)
        nodes_by_cluster.setdefault(ci, []).append(n)

    total_accounts = len(nodes)
    total_videos = 0
    try:
        conn = sqlite3.connect(DB_PATH)
        total_videos = conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
        conn.close()
    except Exception:
        pass

    clusters_out = []
    for c in clusters_raw:
        ci = c["cluster_index"]
        color = MACRO_COLORS.get(ci, "#888888")

        # Resolve name: graph.json name → cluster_names.json fingerprint lookup → fallback
        cluster_name = c.get("name")
        if not cluster_name:
            fp = _fingerprint_key(c.get("top_hashtags", []))
            entry = cluster_names.get("macro", {}).get(fp)
            if entry:
                cluster_name = entry.get("name")
        if not cluster_name:
            cluster_name = f"Cluster {ci}"

        cluster_desc = c.get("description")
        if not cluster_desc:
            fp = _fingerprint_key(c.get("top_hashtags", []))
            entry = cluster_names.get("macro", {}).get(fp)
            if entry:
                cluster_desc = entry.get("description")

        subclusters_out = []
        for sc in c.get("subclusters") or []:
            subclusters_out.append(build_subcluster(sc, nodes_by_sub, db_accounts))

        # Top bridges at macro level
        bridge_accounts = [
            build_account_card(n, db_accounts)
            for n in nodes_by_cluster.get(ci, [])
            if n.get("is_bridge")
        ]

        clusters_out.append({
            "id": str(ci),
            "cluster_index": ci,
            "fingerprint_id": c.get("fingerprint_id"),
            "name": cluster_name,
            "description": cluster_desc,
            "color": color,
            "size": c.get("size", 0),
            "density": round(c.get("internal_density", 0), 3),
            "reach": c.get("total_followers", 0),
            "top_hashtags": (c.get("top_hashtags") or [])[:10],
            "bridge_accounts": bridge_accounts,
            "subclusters": subclusters_out,
        })

    # ── account_memberships: {username: [cluster_path]} ──────────────────────
    # Enables v4 viz to know which level an account belongs to at each depth.
    # e.g. {"user123": ["1", "1b", "1b·2"]}
    account_memberships = {}

    def _index_memberships(cluster_id, subclusters, accounts_list):
        for acc in accounts_list:
            uname = acc.get("username")
            if uname:
                account_memberships.setdefault(uname, [])
                if cluster_id not in account_memberships[uname]:
                    account_memberships[uname].append(cluster_id)

    def _walk_memberships(cluster):
        cid = cluster["id"]
        _index_memberships(cid, [], cluster.get("bridge_accounts", []))
        for sub in cluster.get("subclusters", []):
            for acc in sub.get("accounts", []):
                uname = acc.get("username")
                if uname:
                    account_memberships.setdefault(uname, [])
                    # Add macro + all parent sub-cluster IDs
                    if cid not in account_memberships[uname]:
                        account_memberships[uname].append(cid)
                    if sub["id"] not in account_memberships[uname]:
                        account_memberships[uname].append(sub["id"])
            _walk_memberships_subs(sub, cid)

    def _walk_memberships_subs(sub, macro_id):
        for child in sub.get("subclusters", []):
            cid = child["id"]
            for acc in child.get("accounts", []):
                uname = acc.get("username")
                if uname:
                    account_memberships.setdefault(uname, [])
                    for pid in [macro_id, sub["id"], cid]:
                        if pid not in account_memberships[uname]:
                            account_memberships[uname].append(pid)
            _walk_memberships_subs(child, macro_id)

    for c in clusters_out:
        _walk_memberships(c)

    output = {
        "meta": {
            "generated": datetime.now(timezone.utc).isoformat(),
            "total_accounts": total_accounts,
            "total_videos": total_videos,
            "macro_clusters": len(clusters_out),
        },
        "clusters": clusters_out,
        "account_memberships": account_memberships,
    }

    with open(OUT_PATH, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"Written: {OUT_PATH}")
    print(f"  {total_accounts} accounts, {total_videos} videos")
    print(f"  {len(clusters_out)} macro-clusters")
    for c in clusters_out:
        print(f"    [{c['id']}] {c['name']} — {c['size']} accounts, {len(c['subclusters'])} sub-clusters")


if __name__ == "__main__":
    main()
