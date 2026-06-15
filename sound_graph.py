"""
sound_graph.py — Community detection on sound co-occurrence.

Builds a graph where:
  Nodes = fully-collected accounts
  Edges = weighted by number of shared audio IDs

Then runs the same two-pass Louvain detection as analyze.py and prints
a side-by-side comparison with the hashtag clusters.

Usage:
    python3 sound_graph.py              # report only
    python3 sound_graph.py --save       # + write data/sound_graph.json
    python3 sound_graph.py --originals  # only use original (user-created) sounds
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import networkx as nx
import community as community_louvain

from db import DB_PATH, get_conn

# ── Thresholds (same as analyze.py where applicable) ─────────────────────────
MIN_EDGE_WEIGHT   = 2    # accounts must share ≥2 sounds to be connected
MIN_BUBBLE_SIZE   = 8
MIN_DENSITY       = 0.03
MIN_SUB_SIZE      = 3
MIN_SUB_DENSITY   = 0.10
MIN_LIFT          = 2.0

# Sounds used by fewer than this many accounts are noise (viral pop songs etc.)
MIN_SOUND_ACCOUNTS = 2

# Markers for user-created original sounds
ORIGINAL_MARKERS = [
    "original sound", "son original", "geluid van", "origineel geluid",
    "оригинальный звук", "звук", "صوت", "صدای اصلی"
]


def is_original(title: str) -> bool:
    t = title.lower()
    return any(m in t for m in ORIGINAL_MARKERS)


# ── Data loading ──────────────────────────────────────────────────────────────

def load_sound_cooccurrence(originals_only: bool = False) -> dict[str, dict]:
    """
    Returns {audio_id: {"title": str, "accounts": set}}
    for all sounds used by fully-collected accounts.
    """
    conn = get_conn()
    rows = conn.execute("""
        SELECT v.audio_id, v.audio_title, v.author
        FROM videos v
        JOIN accounts a ON v.author = a.username
        WHERE a.fully_collected = 1
          AND v.audio_id   != ''
          AND v.audio_title != ''
    """).fetchall()
    conn.close()

    sounds: dict[str, dict] = defaultdict(lambda: {"title": "", "accounts": set()})
    for row in rows:
        aid   = row["audio_id"]
        title = row["audio_title"]
        if originals_only and not is_original(title):
            continue
        sounds[aid]["title"] = title
        sounds[aid]["accounts"].add(row["author"])

    # Drop sounds used by only 1 account — no co-occurrence possible
    return {aid: v for aid, v in sounds.items() if len(v["accounts"]) >= MIN_SOUND_ACCOUNTS}


def load_account_metadata() -> dict[str, dict]:
    """Returns {username: {followers, hashtags}} for fully-collected accounts."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT username, followers, hashtags
        FROM accounts
        WHERE fully_collected = 1
    """).fetchall()
    conn.close()
    return {
        r["username"]: {
            "followers": r["followers"],
            "hashtags":  json.loads(r["hashtags"] or "[]"),
        }
        for r in rows
    }


# ── Graph construction ────────────────────────────────────────────────────────

def build_sound_graph(originals_only: bool = False) -> nx.Graph:
    sounds   = load_sound_cooccurrence(originals_only)
    metadata = load_account_metadata()

    G = nx.Graph()
    for username, meta in metadata.items():
        G.add_node(username, **meta)

    # Inverted index already done — sounds dict has accounts per sound
    for aid, info in sounds.items():
        users = list(info["accounts"])
        for i in range(len(users)):
            for j in range(i + 1, len(users)):
                a, b = users[i], users[j]
                if not G.has_node(a) or not G.has_node(b):
                    continue
                if G.has_edge(a, b):
                    G[a][b]["weight"]      += 1
                    G[a][b]["shared_sounds"].append(info["title"])
                else:
                    G.add_edge(a, b, weight=1, shared_sounds=[info["title"]])

    print(f"Sound graph (raw): {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    weak = [(a, b) for a, b, d in G.edges(data=True) if d["weight"] < MIN_EDGE_WEIGHT]
    G.remove_edges_from(weak)
    isolated = [n for n in G.nodes() if G.degree(n) == 0]
    G.remove_nodes_from(isolated)

    print(f"Sound graph (weight≥{MIN_EDGE_WEIGHT}): {G.number_of_nodes()} nodes, "
          f"{G.number_of_edges()} edges")
    return G


# ── Cluster analysis ──────────────────────────────────────────────────────────

def characterize_cluster(cid: int, members: list, G: nx.Graph, metadata: dict) -> dict:
    subG      = G.subgraph(members)
    n         = len(members)
    possible  = n * (n - 1) / 2
    density   = subG.number_of_edges() / possible if possible > 0 else 0

    top_accounts = sorted(members, key=lambda u: metadata.get(u, {}).get("followers", 0), reverse=True)[:6]
    total_followers = sum(metadata.get(u, {}).get("followers", 0) for u in members)

    # Top sounds shared within this cluster
    sound_counter: Counter = Counter()
    for a, b, d in subG.edges(data=True):
        for s in d.get("shared_sounds", []):
            sound_counter[s] += 1
    top_sounds = [s for s, _ in sound_counter.most_common(5)]

    # Top hashtags of members (for comparison with hashtag graph)
    tag_counter: Counter = Counter()
    for u in members:
        tag_counter.update(metadata.get(u, {}).get("hashtags", []))
    top_hashtags = [t for t, _ in tag_counter.most_common(8)]

    return {
        "cluster_id":     cid,
        "size":           n,
        "members":        members,
        "density":        density,
        "top_accounts":   top_accounts,
        "top_sounds":     top_sounds,
        "top_hashtags":   top_hashtags,
        "total_followers": total_followers,
        "valid":          n >= MIN_BUBBLE_SIZE and density >= MIN_DENSITY,
    }


def detect_subclusters(members: list, G: nx.Graph, metadata: dict, depth: int = 1) -> list[dict]:
    if depth > 2 or len(members) < MIN_SUB_SIZE * 2:
        return []

    subG = G.subgraph(members).copy()
    if subG.number_of_edges() == 0:
        return []

    partition = community_louvain.best_partition(subG, weight="weight")
    sub_by_id: dict[int, list] = {}
    for node, cid in partition.items():
        sub_by_id.setdefault(cid, []).append(node)

    if len(sub_by_id) <= 1:
        return []

    effective_lift = MIN_LIFT + (depth - 1)
    valid_subs = []

    for cid, sub_members in sub_by_id.items():
        n = len(sub_members)
        if n < MIN_SUB_SIZE:
            continue
        sub_subG = subG.subgraph(sub_members)
        possible = n * (n - 1) / 2
        density  = sub_subG.number_of_edges() / possible if possible > 0 else 0
        if density < MIN_SUB_DENSITY:
            continue

        # Distinctive sounds: lift vs rest of macro-cluster
        rest   = [m for m in members if m not in set(sub_members)]
        n_rest = len(rest)

        sub_sounds: Counter  = Counter()
        rest_sounds: Counter = Counter()
        for a, b, d in sub_subG.edges(data=True):
            for s in d.get("shared_sounds", []):
                sub_sounds[s] += 1
        rest_subG = subG.subgraph(rest)
        for a, b, d in rest_subG.edges(data=True):
            for s in d.get("shared_sounds", []):
                rest_sounds[s] += 1

        n_sub = len(sub_members)
        distinctive = []
        for sound, count in sub_sounds.most_common(10):
            freq_sub  = count / n_sub
            freq_rest = rest_sounds.get(sound, 0) / max(n_rest, 1)
            lift = freq_sub / (freq_rest + 0.01)
            if lift >= effective_lift:
                distinctive.append(sound)

        if not distinctive:
            continue

        top_accounts = sorted(
            sub_members, key=lambda u: metadata.get(u, {}).get("followers", 0), reverse=True
        )[:4]
        top_hashtags = []
        tag_c: Counter = Counter()
        for u in sub_members:
            tag_c.update(metadata.get(u, {}).get("hashtags", []))
        top_hashtags = [t for t, _ in tag_c.most_common(4)]

        children = detect_subclusters(sub_members, G, metadata, depth + 1)
        valid_subs.append({
            "depth":       depth,
            "size":        n,
            "density":     density,
            "top_accounts": top_accounts,
            "top_sounds":  distinctive[:3],
            "top_hashtags": top_hashtags,
            "subclusters": children,
        })

    valid_subs.sort(key=lambda x: x["size"], reverse=True)
    return valid_subs


# ── Comparison with hashtag graph ─────────────────────────────────────────────

def load_hashtag_clusters() -> list[dict]:
    """Load valid clusters from the existing graph.json for comparison."""
    path = DB_PATH.parent / "graph.json"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("clusters", [])


def overlap_score(sound_members: set, hashtag_members: set) -> float:
    if not sound_members or not hashtag_members:
        return 0.0
    return len(sound_members & hashtag_members) / len(sound_members | hashtag_members)


# ── Report ────────────────────────────────────────────────────────────────────

def generate_report(save: bool = False, originals_only: bool = False):
    mode = "ORIGINALS ONLY" if originals_only else "ALL SOUNDS"
    print(f"\n{'=' * 65}")
    print(f"SOUND GRAPH REPORT — Dutch TikTok  [{mode}]")
    print(f"{'=' * 65}\n")

    G = build_sound_graph(originals_only)
    metadata = load_account_metadata()

    if G.number_of_nodes() < 10:
        print("Graph too small. Collect more accounts first.")
        return

    # Macro Louvain
    partition = community_louvain.best_partition(G, weight="weight")
    clusters_by_id: dict[int, list] = {}
    for username, cid in partition.items():
        clusters_by_id.setdefault(cid, []).append(username)

    clusters = []
    for cid, members in clusters_by_id.items():
        c = characterize_cluster(cid, members, G, metadata)
        clusters.append(c)
    clusters.sort(key=lambda x: x["size"], reverse=True)

    valid   = [c for c in clusters if c["valid"]]
    invalid = [c for c in clusters if not c["valid"]]

    # Sub-cluster detection
    for c in valid:
        c["subclusters"] = detect_subclusters(c["members"], G, metadata)

    # Load hashtag clusters for comparison
    hashtag_clusters = load_hashtag_clusters()
    ht_sets = [
        (hc.get("name") or f"HT-Cluster {hc['cluster_index']}",
         set(hc.get("top_accounts", [])))
        for hc in hashtag_clusters
    ]

    print(f"{len(valid)} sound-based bubbles | {len(invalid)} below threshold\n")

    for i, c in enumerate(valid, 1):
        sounds_str = " | ".join(f'"{s[:40]}"' for s in c["top_sounds"]) or "—"
        tags_str   = ", ".join(f"#{t}" for t in c["top_hashtags"][:5]) or "—"
        top_str    = ", ".join(f"@{u}" for u in c["top_accounts"])

        print(f"Sound Cluster {i}  (id={c['cluster_id']})")
        print(f"  Size             : {c['size']} accounts")
        print(f"  Total reach      : {c['total_followers']:,} followers")
        print(f"  Internal density : {c['density']:.1%}")
        print(f"  Top sounds       : {sounds_str}")
        print(f"  Top hashtags     : {tags_str}")
        print(f"  Top accounts     : {top_str}")

        # Overlap with hashtag clusters
        sound_set = set(c["members"])
        if ht_sets:
            print(f"  Hashtag overlap  :", end="")
            overlaps = sorted(
                [(name, overlap_score(sound_set, ht_set)) for name, ht_set in ht_sets],
                key=lambda x: x[1], reverse=True,
            )
            first = True
            for name, score in overlaps:
                if score > 0.05:
                    prefix = " " if first else "                   "
                    print(f"{prefix}{name}: {score:.0%}")
                    first = False
            if first:
                print(" <5% overlap with all hashtag clusters  ← NEW SIGNAL")

        # Sub-clusters
        subs = c.get("subclusters", [])
        if subs:
            print()
            for j, s in enumerate(subs):
                label     = f"{i}{chr(ord('a') + j)}"
                sounds_s  = " | ".join(f'"{x[:35]}"' for x in s["top_sounds"]) or "—"
                tags_s    = ", ".join(f"#{t}" for t in s["top_hashtags"][:3]) or "—"
                top_s     = ", ".join(f"@{u}" for u in s["top_accounts"])
                print(f"  └─ Sub {label}  ({s['size']} accounts, {s['density']:.0%} density)")
                print(f"       Sounds    : {sounds_s}")
                print(f"       Hashtags  : {tags_s}")
                print(f"       Accounts  : {top_s}")
                if s.get("subclusters"):
                    for k, ss in enumerate(s["subclusters"]):
                        sublabel  = f"{label}·{k+1}"
                        sounds_ss = " | ".join(f'"{x[:30]}"' for x in ss["top_sounds"]) or "—"
                        print(f"       └─ {sublabel}  ({ss['size']} accounts)")
                        print(f"            Sounds : {sounds_ss}")
                print()
        else:
            print(f"  (no distinct sub-clusters)")
        print()

    if invalid:
        print(f"--- {len(invalid)} clusters below threshold ---")
        for c in invalid:
            print(f"  id={c['cluster_id']}: {c['size']} accounts, density={c['density']:.1%}")

    # Cross-graph summary
    print(f"\n{'=' * 65}")
    print("CROSS-GRAPH COMPARISON SUMMARY")
    print(f"{'=' * 65}")
    print(f"  Hashtag graph : {sum(hc['size'] for hc in hashtag_clusters)} nodes in "
          f"{len(hashtag_clusters)} clusters")
    print(f"  Sound graph   : {G.number_of_nodes()} nodes in {len(valid)} clusters")

    all_sound_members = set().union(*(set(c["members"]) for c in valid))
    all_ht_members    = set().union(*(ht_set for _, ht_set in ht_sets))
    in_both   = all_sound_members & all_ht_members
    sound_only = all_sound_members - all_ht_members
    ht_only    = all_ht_members - all_sound_members

    print(f"\n  In both graphs : {len(in_both)} accounts")
    print(f"  Sound-only     : {len(sound_only)} accounts  ← only visible via sound")
    print(f"  Hashtag-only   : {len(ht_only)} accounts  ← only visible via hashtags")

    if sound_only:
        sound_only_top = sorted(
            sound_only,
            key=lambda u: metadata.get(u, {}).get("followers", 0),
            reverse=True,
        )[:8]
        print(f"\n  Top sound-only accounts (not in hashtag graph):")
        for u in sound_only_top:
            f = metadata.get(u, {}).get("followers", 0)
            print(f"    @{u} ({f:,} followers)")

    print(f"\n{'=' * 65}\n")

    if save:
        out = {
            "mode": mode,
            "graph_nodes": G.number_of_nodes(),
            "graph_edges": G.number_of_edges(),
            "clusters": [
                {
                    "cluster_id":      c["cluster_id"],
                    "size":            c["size"],
                    "density":         round(c["density"], 3),
                    "top_sounds":      c["top_sounds"],
                    "top_hashtags":    c["top_hashtags"],
                    "top_accounts":    c["top_accounts"],
                    "total_followers": c["total_followers"],
                    "members":         c["members"],
                    "subclusters":     c.get("subclusters", []),
                }
                for c in valid
            ],
        }
        path = DB_PATH.parent / "sound_graph.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, ensure_ascii=False)
        print(f"Saved to {path}")


if __name__ == "__main__":
    _save      = "--save"      in sys.argv
    _originals = "--originals" in sys.argv
    generate_report(save=_save, originals_only=_originals)
