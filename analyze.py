"""
analyze.py — Two-level hierarchical community detection and bubble report.

Pass 1 (macro): Louvain on the full graph → top-level bubbles.
Pass 2 (micro): Louvain (or Leiden if available) on each valid macro-cluster
                → sub-bubbles with distinctive hashtag signatures.

The report IS the validation gate:
  A cluster only becomes a "bubble" when you can name it.

Usage:
    python analyze.py
"""

import hashlib
import json
import logging
import sys
from collections import Counter
from datetime import datetime, timezone

import networkx as nx
import community as community_louvain

from db import get_all_accounts, get_stats, DB_PATH, get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
logger = logging.getLogger(__name__)

# ── Optional Leiden import ────────────────────────────────────────────────────
try:
    import igraph as ig
    import leidenalg
    _LEIDEN_AVAILABLE = True
except ImportError:
    _LEIDEN_AVAILABLE = False
    print("ℹ️  leidenalg not installed — using Louvain for sub-cluster pass (pip install leidenalg igraph to upgrade)")

# ── Thresholds ────────────────────────────────────────────────────────────────

# Macro-cluster validity (unchanged)
MIN_BUBBLE_SIZE     = 8     # fewer accounts = noise
MIN_INTERNAL_DENSITY = 0.03  # 3% of possible internal edges

# Sub-cluster validity
MIN_SUB_SIZE        = 3     # minimum accounts per sub-cluster
MIN_SUB_DENSITY     = 0.10  # 10% internal density
MIN_LIFT            = 2.0   # hashtag must be 2× more frequent vs rest of macro

# Recursive detection — no hard depth cap; splits stop when signal disappears
MAX_DETECTION_DEPTH  = 8    # safety ceiling only — in practice lift threshold stops splits far earlier
LIFT_DEPTH_INCREMENT = 0.8  # lift threshold rises by this per depth level (tightens signal naturally)
MIN_SPLIT_CONFIDENCE = 0.05 # don't recurse if confidence falls below this — signal too weak
MIN_VARIANCE_RATIO   = 0.10 # a split must reduce within-cluster hashtag entropy by ≥10%

# Fit score / counteralgorithm
FIT_OUTLIER_THRESHOLD = 0.15  # accounts using < 15% of cluster's top tags are flagged
MAX_OUTLIERS_SHOWN    = 4     # outliers shown per cluster in the report


# ── Graph construction (unchanged) ───────────────────────────────────────────

# Dutch stopwords to strip from caption text — high-frequency words with no signal value
_NL_STOPWORDS = {
    "de", "het", "een", "van", "in", "is", "en", "op", "dat", "te", "zijn",
    "voor", "met", "aan", "er", "maar", "ze", "bij", "ook", "als", "al",
    "naar", "om", "dit", "niet", "meer", "was", "we", "je", "ik", "hij",
    "had", "door", "uit", "zo", "over", "dan", "wat", "nog", "heeft",
    "wordt", "worden", "worden", "zich", "worden", "heb", "heeft",
    "kunnen", "gaan", "komen", "zal", "zou", "hun", "hen", "die",
    "werd", "deze", "nu", "mijn", "jouw", "zijn", "haar", "ons", "onze",
    "the", "a", "is", "to", "of", "and", "in", "for", "on", "with",
}

# Minimum caption token length — filters out emoji remnants and noise
_MIN_TOKEN_LEN = 4

# Weight multipliers: how much a caption keyword counts vs a hashtag
# Hashtags are explicit intent; captions are contextual — weight them lower
_HASHTAG_WEIGHT = 3
_CAPTION_WEIGHT = 1
_BIO_WEIGHT     = 2


def _load_video_hashtags(usernames: set) -> dict[str, list[str]]:
    """
    Build top-50 hashtag profiles from the videos table for a set of accounts.
    More complete than accounts.hashtags which was capped at collection time.
    """
    conn = get_conn()
    rows = conn.execute(
        "SELECT author, hashtags FROM videos WHERE author IN ({})".format(
            ",".join("?" * len(usernames))
        ),
        list(usernames),
    ).fetchall()
    conn.close()

    counts: dict[str, Counter] = {}
    for author, tags_json in rows:
        if author not in counts:
            counts[author] = Counter()
        counts[author].update(json.loads(tags_json))

    return {u: [t for t, _ in c.most_common(50)] for u, c in counts.items()}


def _load_caption_keywords(usernames: set) -> dict[str, list[str]]:
    """
    Extract top-30 content keywords from video descriptions, stripping #hashtags
    (already captured separately), @mentions, URLs, stopwords, and short tokens.

    Used as a supplementary co-occurrence signal for accounts that don't use
    hashtags — institutional accounts, party channels, media outlets.
    """
    import re
    conn = get_conn()
    rows = conn.execute(
        "SELECT author, description FROM videos WHERE author IN ({}) AND description IS NOT NULL".format(
            ",".join("?" * len(usernames))
        ),
        list(usernames),
    ).fetchall()
    conn.close()

    counts: dict[str, Counter] = {}
    for author, description in rows:
        if not description:
            continue
        # Strip #tags, @mentions, URLs, punctuation; lowercase
        text = re.sub(r'#\w+', '', description)
        text = re.sub(r'@\w+', '', text)
        text = re.sub(r'https?://\S+', '', text)
        text = re.sub(r'[^\w\s]', ' ', text)
        tokens = [
            t.lower() for t in text.split()
            if len(t) >= _MIN_TOKEN_LEN and t.lower() not in _NL_STOPWORDS
        ]
        if not tokens:
            continue
        if author not in counts:
            counts[author] = Counter()
        counts[author].update(tokens)

    return {u: [t for t, _ in c.most_common(30)] for u, c in counts.items()}


def _bio_keywords(bio: str) -> list[str]:
    """Extract meaningful tokens from a bio string."""
    import re
    if not bio:
        return []
    text = re.sub(r'[^\w\s]', ' ', bio)
    return [
        t.lower() for t in text.split()
        if len(t) >= _MIN_TOKEN_LEN and t.lower() not in _NL_STOPWORDS
    ]


def _add_cooccurrence(G: nx.Graph, token_to_accounts: dict[str, list], weight: int):
    """Connect all account pairs sharing a token, adding edge weight."""
    for token, users in token_to_accounts.items():
        if len(users) < 2:
            continue
        for i in range(len(users)):
            for j in range(i + 1, len(users)):
                a, b = users[i], users[j]
                if G.has_edge(a, b):
                    G[a][b]["weight"] += weight
                else:
                    G.add_edge(a, b, weight=weight)


def build_graph() -> nx.Graph:
    """
    Build an undirected weighted graph from three co-occurrence signals:

      1. Hashtags (weight ×3)  — explicit topic intent, strongest signal
      2. Caption keywords (weight ×1) — covers accounts that don't use hashtags
      3. Bio keywords (weight ×2) — stable identity signal

    Edge weight reflects shared signal across all three sources.
    Pruning is adaptive: accounts with very few hashtags (≤3 total) use a
    lower threshold (weight ≥ 1) so they can still connect via captions/bio.
    Accounts with richer hashtag profiles keep the standard threshold (weight ≥ 2).

    Only Dutch fully-collected accounts (is_dutch=1) are included.
    """
    accounts = get_all_accounts()

    dutch_accounts = [
        a for a in accounts
        if a.get("fully_collected") and a.get("is_dutch", 1)
    ]

    usernames = {a["username"] for a in dutch_accounts}
    video_hashtags   = _load_video_hashtags(usernames)
    caption_keywords = _load_caption_keywords(usernames)

    G = nx.Graph()
    sparse_nodes = set()  # accounts with very few hashtags — get looser pruning

    for data in dutch_accounts:
        uname  = data["username"]
        stored = json.loads(data.get("hashtags") or "[]")
        tags   = video_hashtags.get(uname) or stored
        bio_kw = _bio_keywords(data.get("bio", ""))

        G.add_node(uname,
            followers=data.get("followers", 0),
            following_count=data.get("following_count", 0),
            bio=data.get("bio", ""),
            verified=data.get("verified", 0),
            hashtags=tags,
            caption_keywords=caption_keywords.get(uname, []),
            bio_keywords=bio_kw,
        )
        if len(tags) <= 3:
            sparse_nodes.add(uname)

    # ── Signal 1: hashtag co-occurrence (weight ×3) ───────────────────────────
    tag_to_accounts: dict[str, list] = {}
    for u in G.nodes():
        for tag in G.nodes[u]["hashtags"]:
            tag_to_accounts.setdefault(tag, []).append(u)
    _add_cooccurrence(G, tag_to_accounts, _HASHTAG_WEIGHT)

    # ── Signal 2: caption keyword co-occurrence (weight ×1) ───────────────────
    cap_to_accounts: dict[str, list] = {}
    for u in G.nodes():
        for kw in G.nodes[u]["caption_keywords"]:
            cap_to_accounts.setdefault(kw, []).append(u)
    _add_cooccurrence(G, cap_to_accounts, _CAPTION_WEIGHT)

    # ── Signal 3: bio keyword co-occurrence (weight ×2) ───────────────────────
    bio_to_accounts: dict[str, list] = {}
    for u in G.nodes():
        for kw in G.nodes[u]["bio_keywords"]:
            bio_to_accounts.setdefault(kw, []).append(u)
    _add_cooccurrence(G, bio_to_accounts, _BIO_WEIGHT)

    logger.info(f"Graph (raw): {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

    # ── Adaptive pruning ──────────────────────────────────────────────────────
    # Sparse accounts (≤3 hashtags): keep edges with weight ≥ 1 so captions/bio
    # can connect them. All others: standard threshold weight ≥ 2 (= 6 in raw
    # weight because hashtags count ×3, so threshold is actually weight ≥ 2
    # hashtag-equivalents = raw weight ≥ 6 for hashtag-rich accounts).
    # Simpler: use weight ≥ 2 raw for sparse, weight ≥ 6 for rich.
    weak = []
    for a, b, d in G.edges(data=True):
        w = d.get("weight", 0)
        both_sparse = a in sparse_nodes and b in sparse_nodes
        if both_sparse:
            if w < 2:   # 2 caption words in common is enough
                weak.append((a, b))
        else:
            if w < 6:   # equivalent to ≥2 shared hashtags
                weak.append((a, b))
    G.remove_edges_from(weak)

    isolated = [n for n in G.nodes() if G.degree(n) == 0]
    G.remove_nodes_from(isolated)

    n_sparse_connected = sum(1 for n in G.nodes() if n in sparse_nodes)
    logger.info(
        f"Graph (adaptive pruning): {G.number_of_nodes()} nodes, "
        f"{G.number_of_edges()} edges  "
        f"({n_sparse_connected} sparse-hashtag accounts connected via captions/bio)"
    )
    return G


# ── Bubble characterization (unchanged) ──────────────────────────────────────

def characterize(cluster_id: int, members: list, G: nx.Graph) -> dict:
    subgraph = G.subgraph(members)
    n = len(members)
    possible_edges = n * (n - 1) / 2
    actual_edges = subgraph.number_of_edges()
    density = actual_edges / possible_edges if possible_edges > 0 else 0

    top_accounts = sorted(members, key=lambda u: G.nodes[u].get("followers", 0), reverse=True)[:10]

    tag_counter = Counter()
    for u in members:
        tag_counter.update(G.nodes[u].get("hashtags", []))
    top_hashtags = [t for t, _ in tag_counter.most_common(10)]

    total_followers = sum(G.nodes[u].get("followers", 0) for u in members)

    external_connections = {}
    for u in members:
        external = [v for v in G.neighbors(u) if v not in set(members)]
        if external:
            external_connections[u] = len(external)
    top_bridges = sorted(external_connections, key=external_connections.get, reverse=True)[:3]

    fit_scores = compute_fit_scores(members, top_hashtags, G)
    confidence = _compute_macro_confidence(n, density)

    return {
        "cluster_id": cluster_id,
        "size": n,
        "members": members,
        "top_accounts": top_accounts,
        "top_hashtags": top_hashtags,
        "internal_density": density,
        "total_followers": total_followers,
        "top_bridges": top_bridges,
        "fit_scores": fit_scores,
        "confidence": confidence,
        "confidence_label": _confidence_label(confidence),
        "passes_size": n >= MIN_BUBBLE_SIZE,
        "passes_density": density >= MIN_INTERNAL_DENSITY,
    }


# ── Validation ───────────────────────────────────────────────────────────────

def bubble_is_valid(c: dict) -> bool:
    return c["passes_size"] and c["passes_density"]


# ── Fit scores + counteralgorithm ─────────────────────────────────────────────

def compute_fit_scores(members: list, top_hashtags: list, G: nx.Graph) -> dict[str, float]:
    """
    For each member compute a fit score: fraction of the cluster's top 10 hashtags
    that this account actually uses.

    1.0 = uses all top cluster tags  → core member, deeply in the bubble
    0.0 = uses none of them          → outlier, possible counter-narrative or border actor

    Fit score is a measure of how well an account "belongs" to its assigned cluster.
    Low-fit accounts are the most interesting: they ended up in this bubble but pull
    toward something else — bridges, contrarians, embedded observers.
    """
    top_set = set(top_hashtags[:10])
    if not top_set:
        return {u: 0.0 for u in members}
    scores = {}
    for u in members:
        user_tags = set(G.nodes[u].get("hashtags", []))
        scores[u] = len(user_tags & top_set) / len(top_set)
    return scores


def find_cross_affinity(
    username: str,
    own_cluster_id: int,
    valid_clusters: list,
    cluster_labels: dict[int, str],
    G: nx.Graph,
) -> tuple[str | None, float]:
    """
    For a low-fit account, find which *other* valid cluster's top hashtags
    this account overlaps with most.

    Returns (label, score) where label is e.g. "Cluster 2" and score is the
    overlap fraction. Returns (None, 0.0) if no meaningful affinity found.
    """
    user_tags = set(G.nodes[username].get("hashtags", []))
    best_score = 0.05   # minimum threshold — below this = no meaningful affinity
    best_label = None

    for c in valid_clusters:
        if c["cluster_id"] == own_cluster_id:
            continue
        other_top = set(c["top_hashtags"][:10])
        if not other_top:
            continue
        score = len(user_tags & other_top) / len(other_top)
        if score > best_score:
            best_score = score
            best_label = cluster_labels[c["cluster_id"]]

    return best_label, best_score


# ── Sub-cluster detection ─────────────────────────────────────────────────────

def _lift_scores(sub_members: list, rest_members: list, G: nx.Graph) -> dict[str, float]:
    """Raw lift dict: {tag: lift_score} for all tags in sub_members."""
    n_sub  = len(sub_members)
    n_rest = len(rest_members)
    if n_sub == 0:
        return {}
    sub_counts, rest_counts = Counter(), Counter()
    for u in sub_members:
        for tag in G.nodes[u].get("hashtags", []):
            sub_counts[tag] += 1
    for u in rest_members:
        for tag in G.nodes[u].get("hashtags", []):
            rest_counts[tag] += 1
    return {
        tag: (count / n_sub) / (rest_counts.get(tag, 0) / max(n_rest, 1) + 0.01)
        for tag, count in sub_counts.items()
    }


def compute_lift(sub_members: list, rest_members: list, G: nx.Graph,
                 min_lift: float = MIN_LIFT) -> list[str]:
    """
    Hashtags distinctive to sub_members vs rest_members.
    Lift = freq_sub / (freq_rest + 0.01 smoothing).
    Returns tags with lift ≥ min_lift, sorted by lift descending.
    min_lift increases with depth to tighten the signal at deeper levels.
    """
    scores = _lift_scores(sub_members, rest_members, G)
    passing = {t: s for t, s in scores.items() if s >= min_lift}
    return sorted(passing, key=passing.get, reverse=True)


# ── Confidence scoring ────────────────────────────────────────────────────────

def _confidence_label(score: float) -> str:
    if score >= 0.60:
        return "HIGH"
    elif score >= 0.30:
        return "MEDIUM"
    else:
        return "LOW"


def _compute_confidence(size: int, density: float, top_lift: float, depth: int) -> float:
    """
    Confidence 0–1 for a sub-cluster at a given depth.

    size_factor    — grows with size, saturates around 25 accounts
    density_factor — internal cohesion (50%+ = full score)
    lift_factor    — distinctiveness vs surroundings (4× threshold = full score)
    depth_penalty  — 15% discount per level deeper than 1

    Deeper layers are inherently less certain: smaller samples, weaker signal.
    The confidence score makes this explicit in the report and JSON.
    """
    size_factor    = min(1.0, (size - MIN_SUB_SIZE) / 22)
    density_factor = min(1.0, density / 0.5)
    lift_factor    = min(1.0, top_lift / (MIN_LIFT * 4))
    depth_penalty  = 0.85 ** (depth - 1)
    return round(size_factor * density_factor * lift_factor * depth_penalty, 3)


def _compute_macro_confidence(size: int, density: float) -> float:
    """Confidence for macro clusters (no lift available, depth = 0)."""
    size_factor    = min(1.0, (size - MIN_BUBBLE_SIZE) / 25)
    density_factor = min(1.0, density / 0.4)
    return round(size_factor * density_factor, 3)


def _fingerprint_id(top_hashtags: list[str]) -> str:
    """
    Stable 8-char ID for a cluster derived from its top-5 hashtags.
    Sorted alphabetically so order changes don't break the ID.
    This is the key that survives Louvain reshuffling between runs.
    """
    key = ",".join(sorted(top_hashtags[:5]))
    return hashlib.md5(key.encode()).hexdigest()[:8]


def _check_publish_threshold(old_graph: dict | None, new_clusters: list) -> tuple[bool, list[str]]:
    """
    Compare new clusters against the previous published graph.json.
    Returns (should_publish, list_of_change_descriptions).

    Triggers a publish if:
      • No previous graph exists (first publish)
      • Any cluster gained or lost ≥ 10 accounts
      • A new depth-1 sub-cluster appeared
      • A previously valid cluster disappeared
    """
    if old_graph is None:
        return True, ["Initial publish"]

    changes = []

    # Index old clusters by fingerprint_id
    old_clusters = {c.get("fingerprint_id"): c for c in old_graph.get("clusters", [])}

    for c in new_clusters:
        fid = c.get("fingerprint_id")
        old_c = old_clusters.get(fid)
        if old_c is None:
            changes.append(f"New cluster appeared: {c.get('name') or fid} ({c['size']} accounts)")
        else:
            delta = c["size"] - old_c["size"]
            if abs(delta) >= 10:
                direction = "+" if delta > 0 else ""
                changes.append(
                    f"{c.get('name') or fid}: {direction}{delta} accounts "
                    f"({old_c['size']} → {c['size']})"
                )

            # Check for new depth-1 sub-clusters
            old_sub_labels = {s["sub_label"] for s in old_c.get("subclusters", [])}
            new_sub_labels = {s["sub_label"] for s in c.get("subclusters", [])}
            for label in new_sub_labels - old_sub_labels:
                sub = next((s for s in c.get("subclusters", []) if s["sub_label"] == label), None)
                hint = sub["label_hint"] if sub else label
                changes.append(f"New sub-cluster {label} in {c.get('name') or fid}: {hint}")

    # Check for clusters that vanished
    new_fids = {c.get("fingerprint_id") for c in new_clusters}
    for fid, old_c in old_clusters.items():
        if fid not in new_fids:
            changes.append(f"Cluster disappeared: {old_c.get('name') or fid}")

    return len(changes) > 0, changes


def make_label_hint(distinctive_tags: list[str]) -> str:
    """Rule-based label from top distinctive hashtags."""
    if not distinctive_tags:
        return "unclear"
    return " / ".join(f"#{t}" for t in distinctive_tags[:3])


def _louvain_partition(subG: nx.Graph) -> dict:
    return community_louvain.best_partition(subG, weight="weight")


def _leiden_partition(subG: nx.Graph) -> dict:
    """Convert nx.Graph to igraph, run Leiden, return {node: community_id}."""
    nodes = list(subG.nodes())
    node_to_idx = {n: i for i, n in enumerate(nodes)}
    ig_graph = ig.Graph()
    ig_graph.add_vertices(len(nodes))
    edges   = [(node_to_idx[a], node_to_idx[b]) for a, b in subG.edges()]
    weights = [subG[a][b].get("weight", 1) for a, b in subG.edges()]
    ig_graph.add_edges(edges)
    partition = leidenalg.find_partition(
        ig_graph, leidenalg.ModularityVertexPartition, weights=weights
    )
    result = {}
    for cid, community_members in enumerate(partition):
        for idx in community_members:
            result[nodes[idx]] = cid
    return result


def _hashtag_entropy(members: list, G: nx.Graph) -> float:
    """
    Shannon entropy of hashtag distribution over members.
    Higher = more diverse/mixed hashtag usage = less coherent cluster.
    Used to measure whether a split meaningfully reduces internal disorder.
    """
    from math import log2
    counts = Counter()
    total = 0
    for u in members:
        for tag in G.nodes[u].get("hashtags", []):
            counts[tag] += 1
            total += 1
    if total == 0:
        return 0.0
    entropy = 0.0
    for count in counts.values():
        p = count / total
        entropy -= p * log2(p)
    return entropy


def _variance_ratio(members: list, sub_groups: list[list], G: nx.Graph) -> float:
    """
    Fraction of hashtag entropy explained by the proposed split.
    variance_ratio = 1 - (weighted avg sub-entropy / parent entropy)

    0.0 = split explains nothing (sub-clusters as mixed as parent)
    1.0 = split perfectly separates hashtag space
    Returns 0.0 if parent entropy is near zero.
    """
    parent_entropy = _hashtag_entropy(members, G)
    if parent_entropy < 0.01:
        return 0.0
    n_total = len(members)
    weighted_sub_entropy = sum(
        (len(sg) / n_total) * _hashtag_entropy(sg, G)
        for sg in sub_groups
    )
    return max(0.0, 1.0 - weighted_sub_entropy / parent_entropy)


def detect_subclusters(members: list, G: nx.Graph, depth: int = 1) -> list[dict]:
    """
    Recursive community detection inside a cluster's subgraph.

    No hard depth cap — splits continue as long as there is genuine signal:
      1. Lift threshold rises by LIFT_DEPTH_INCREMENT each level
         → eventually no cluster passes, naturally stopping recursion
      2. Variance ratio check: split must reduce within-cluster hashtag
         entropy by ≥ MIN_VARIANCE_RATIO — prevents splits that merely
         rearrange noise without revealing real sub-structure
      3. Confidence check: if computed confidence < MIN_SPLIT_CONFIDENCE,
         the signal is too weak to call it a bubble

    Returns valid sub-clusters sorted by size descending.
    Each sub-cluster dict contains its own "subclusters" list (possibly empty).
    """
    if depth > MAX_DETECTION_DEPTH:
        return []
    if len(members) < MIN_SUB_SIZE * 2:
        return []

    subG = G.subgraph(members).copy()
    partition = _leiden_partition(subG) if _LEIDEN_AVAILABLE else _louvain_partition(subG)

    sub_by_id: dict[int, list] = {}
    for node, cid in partition.items():
        sub_by_id.setdefault(cid, []).append(node)

    if len(sub_by_id) <= 1:
        return []

    # Variance check: does this split actually explain structure?
    all_sub_groups = list(sub_by_id.values())
    vr = _variance_ratio(members, all_sub_groups, G)
    if vr < MIN_VARIANCE_RATIO:
        logger.debug(f"  Depth {depth}: variance ratio {vr:.3f} < {MIN_VARIANCE_RATIO} — skipping split")
        return []

    # Lift threshold rises each level — deeper splits need stronger signal
    effective_min_lift = MIN_LIFT + LIFT_DEPTH_INCREMENT * (depth - 1)

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

        rest = [m for m in members if m not in set(sub_members)]

        lift_dict   = _lift_scores(sub_members, rest, G)
        distinctive = sorted(
            (t for t, s in lift_dict.items() if s >= effective_min_lift),
            key=lambda t: lift_dict[t], reverse=True,
        )
        if not distinctive:
            continue

        top_lift   = lift_dict[distinctive[0]]
        confidence = _compute_confidence(n, density, top_lift, depth)

        # Confidence gate — stop here if signal is too weak
        if confidence < MIN_SPLIT_CONFIDENCE:
            logger.debug(f"  Depth {depth}: confidence {confidence:.3f} < {MIN_SPLIT_CONFIDENCE} — not splitting")
            continue

        top_accounts = sorted(
            sub_members, key=lambda u: G.nodes[u].get("followers", 0), reverse=True
        )[:4]

        # Recurse — will stop naturally when signal disappears
        child_subs = detect_subclusters(sub_members, G, depth=depth + 1)

        valid_subs.append({
            "sub_id":               cid,
            "members":              sub_members,
            "size":                 n,
            "density":              density,
            "depth":                depth,
            "distinctive_hashtags": distinctive[:3],
            "top_accounts":         top_accounts,
            "label_hint":           make_label_hint(distinctive[:3]),
            "confidence":           confidence,
            "confidence_label":     _confidence_label(confidence),
            "variance_ratio":       round(vr, 3),
            "subclusters":          child_subs,
        })

    valid_subs.sort(key=lambda x: x["size"], reverse=True)
    return valid_subs


# ── JSON export ──────────────────────────────────────────────────────────────

def _write_graph_json(
    G: nx.Graph,
    valid: list,
    invalid: list,
    algo_label: str,
    publish: bool = False,
    publish_changes: list[str] | None = None,
):
    """
    Write data/graph.json — the single file the web visualization reads.

    Structure:
      meta      — run metadata (timestamp, algo, counts)
      clusters  — macro bubbles with nested sub-clusters; name=null until you fill in B
      nodes     — every graph node with cluster assignment, fit score, outlier flag
      edges     — all weight≥2 co-occurrence edges
    """
    out_path = DB_PATH.parent / "graph.json"

    # ── Build node → cluster lookup ───────────────────────────────────────────
    # valid clusters get a display index (1, 2, …); invalid clusters get None
    valid_idx   = {c["cluster_id"]: i for i, c in enumerate(valid, 1)}
    node_cluster_idx: dict[str, int | None] = {}
    node_sub_label:   dict[str, str]        = {}
    node_is_bridge:   dict[str, bool]       = {}
    node_fit_score:   dict[str, float]      = {}
    node_own_cluster: dict[str, dict]       = {}

    all_clusters = valid + invalid
    for c in all_clusters:
        idx = valid_idx.get(c["cluster_id"])   # None for invalid clusters
        bridge_set = set(c.get("top_bridges", []))
        for u in c["members"]:
            node_cluster_idx[u] = idx
            node_is_bridge[u]   = u in bridge_set
            node_fit_score[u]   = c["fit_scores"].get(u, 0.0)
            node_own_cluster[u] = c

    def _assign_labels(subs: list, parent_label: str):
        """Recursively assign sub-cluster labels to nodes (deepest wins)."""
        for j, s in enumerate(subs):
            label = (f"{parent_label}{chr(ord('a') + j)}"
                     if s["depth"] == 1 else f"{parent_label}·{j + 1}")
            for u in s["members"]:
                node_sub_label[u] = label        # overwritten by deeper level if it exists
            _assign_labels(s.get("subclusters", []), label)

    for i, c in enumerate(valid, 1):
        _assign_labels(c.get("subclusters", []), str(i))

    # ── Nodes ─────────────────────────────────────────────────────────────────
    nodes = []
    for u in G.nodes():
        data      = G.nodes[u]
        fit       = node_fit_score.get(u, 0.0)
        cluster_i = node_cluster_idx.get(u)
        nodes.append({
            "id":               u,
            "followers":        data.get("followers", 0),
            "bio":              data.get("bio", ""),
            "hashtags":         data.get("hashtags", [])[:15],
            "cluster_index":    cluster_i,           # None = below threshold
            "sub_cluster_label": node_sub_label.get(u),
            "fit_score":        round(fit, 3),
            "is_outlier":       fit < FIT_OUTLIER_THRESHOLD and cluster_i is not None,
            "is_bridge":        node_is_bridge.get(u, False),
        })
    nodes.sort(key=lambda n: (n["cluster_index"] or 99, -n["followers"]))

    # ── Edges ─────────────────────────────────────────────────────────────────
    edges = [
        {"source": a, "target": b, "weight": d.get("weight", 1)}
        for a, b, d in G.edges(data=True)
    ]

    # ── Clusters (recursive serializer) ──────────────────────────────────────
    def _serialize_subs(subs: list, parent_label: str) -> list:
        out = []
        for j, s in enumerate(subs):
            label = (f"{parent_label}{chr(ord('a') + j)}"
                     if s["depth"] == 1 else f"{parent_label}·{j + 1}")
            out.append({
                "sub_label":            label,
                "depth":                s["depth"],
                "size":                 s["size"],
                "density":              round(s["density"], 3),
                "confidence":           s["confidence"],
                "confidence_label":     s["confidence_label"],
                "distinctive_hashtags": s["distinctive_hashtags"],
                "top_accounts":         s["top_accounts"],
                "label_hint":           s["label_hint"],
                "name":                 None,
                "description":          None,
                "subclusters":          _serialize_subs(s.get("subclusters", []), label),
            })
        return out

    def _count_all_subs(subs: list) -> int:
        return sum(1 + _count_all_subs(s.get("subclusters", [])) for s in subs)

    clusters_out = []
    for i, c in enumerate(valid, 1):
        subs_out = _serialize_subs(c.get("subclusters", []), str(i))
        clusters_out.append({
            "cluster_index":    i,
            "cluster_id":       c["cluster_id"],
            "fingerprint_id":   _fingerprint_id(c["top_hashtags"]),
            "name":             None,
            "description":      None,
            "confidence":       c["confidence"],
            "confidence_label": c["confidence_label"],
            "size":             c["size"],
            "internal_density": round(c["internal_density"], 3),
            "total_followers":  c["total_followers"],
            "top_hashtags":     c["top_hashtags"],
            "top_accounts":     c["top_accounts"][:6],
            "top_bridges":      c["top_bridges"],
            "subclusters":      subs_out,
        })

    total_subs = sum(_count_all_subs(c.get("subclusters", [])) for c in valid)

    # ── Preserve version history from previous graph.json ────────────────────
    existing_versions = []
    if out_path.exists():
        try:
            with open(out_path, encoding="utf-8") as f:
                old = json.load(f)
            existing_versions = old.get("meta", {}).get("versions", [])
        except Exception:
            pass

    # Append a new version record if this is a --publish run
    versions = list(existing_versions)
    if publish and publish_changes:
        versions.append({
            "v":              len(versions) + 1,
            "date":           datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "accounts":       G.number_of_nodes(),
            "macro_clusters": len(valid),
            "sub_clusters":   total_subs,
            "changes":        publish_changes,
        })
        logger.info(f"📌 Published v{len(versions)}: {publish_changes}")

    # ── Meta ──────────────────────────────────────────────────────────────────
    output = {
        "meta": {
            "generated_at":       datetime.now(timezone.utc).isoformat(),
            "algo":               algo_label,
            "graph_nodes":        G.number_of_nodes(),
            "graph_edges":        G.number_of_edges(),
            "macro_clusters":     len(valid),
            "total_sub_clusters": total_subs,
            "max_depth":          MAX_DETECTION_DEPTH,
            "below_threshold":    len(invalid),
            "versions":           versions,
        },
        "clusters": clusters_out,
        "nodes":    nodes,
        "edges":    edges,
    }

    # ── Merge human-assigned names from cluster_names.json ────────────────────
    # Names are matched by HASHTAG FINGERPRINT, not position label.
    # This is robust to Louvain reshuffling between runs.
    # A cluster is named if ANY of the fingerprint hashtags appear in its
    # distinctive_hashtags or top_hashtags list.
    names_path = DB_PATH.parent / "cluster_names.json"
    if names_path.exists():
        try:
            with open(names_path, encoding="utf-8") as f:
                names = json.load(f)

            def _parse_entry(value) -> tuple:
                """Support both old (string) and new (object) formats."""
                if isinstance(value, str):
                    return value, None
                elif isinstance(value, dict):
                    return value.get("name"), value.get("description")
                return None, None

            macro_fingerprints = {
                frozenset(k.split(",")): _parse_entry(v)
                for k, v in names.get("macro", {}).items()
                if not k.startswith("_")
            }
            sub_fingerprints = {
                frozenset(k.split(",")): _parse_entry(v)
                for k, v in names.get("sub", {}).items()
                if not k.startswith("_")
            }

            def _match_entry(tag_set: set, fingerprints: dict) -> tuple:
                """
                Return (name, description) for best-matching fingerprint.
                Requires at least max(1, len(fp)-1) tags to match — prevents
                single-hashtag false positives for 3-token fingerprints.
                """
                best_entry, best_score = (None, None), 0
                for fp, entry in fingerprints.items():
                    score     = len(fp & tag_set)
                    threshold = max(1, len(fp) - 1)   # 3-tag fp needs 2 hits
                    if score >= threshold and score > best_score:
                        best_entry, best_score = entry, score
                return best_entry

            def _apply_names_recursive(clusters_or_subs: list, fingerprints: dict,
                                       tag_key: str = "distinctive_hashtags"):
                for item in clusters_or_subs:
                    tags = set(item.get(tag_key, []) + item.get("top_hashtags", []))
                    name, desc = _match_entry(tags, fingerprints)
                    item["name"]        = name
                    item["description"] = desc
                    _apply_names_recursive(
                        item.get("subclusters", []), fingerprints, tag_key
                    )

            _apply_names_recursive(output["clusters"], macro_fingerprints,
                                   tag_key="top_hashtags")
            for c in output["clusters"]:
                _apply_names_recursive(c.get("subclusters", []), sub_fingerprints)

            named_count = sum(
                1 for c in output["clusters"]
                if c.get("name") or any(
                    s.get("name") for s in c.get("subclusters", [])
                )
            )
            logger.info(f"Merged cluster names from cluster_names.json "
                        f"({named_count} clusters matched)")
        except Exception as e:
            logger.warning(f"Could not read cluster_names.json: {e}")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    logger.info(f"Exported → {out_path}  "
                f"({len(nodes)} nodes · {len(edges)} edges · {len(clusters_out)} clusters)")
    print(f"\n  Exported: {out_path}")
    print(f"  {len(nodes)} nodes · {len(edges)} edges · {len(clusters_out)} macro clusters · "
          f"{output['meta']['total_sub_clusters']} sub-clusters (all depths)")
    if not names_path.exists() or not any(
        c.get("name") for c in output["clusters"]
    ):
        print(f"  → Open data/cluster_names.json and add one-sentence names for each cluster.")


# ── Recursive report printer ──────────────────────────────────────────────────

def _print_sub_tree(subs: list, parent_label: str, indent: int = 2):
    """
    Recursively print the sub-cluster tree with increasing indentation.

    Depth 1  →  "└─ Sub-cluster 1a"
    Depth 2  →     "└─ Sub-sub  1a·1"
    Depth 3  →        "└─ Sub³   1a·1·1"
    """
    depth_prefix = {1: "Sub-cluster", 2: "Sub-sub    ", 3: "Sub³       "}
    for j, s in enumerate(subs):
        if s["depth"] == 1:
            label = f"{parent_label}{chr(ord('a') + j)}"
        else:
            label = f"{parent_label}·{j + 1}"

        pad       = " " * indent
        dist_str  = ", ".join(f"#{t}" for t in s["distinctive_hashtags"]) or "—"
        sub_top   = ", ".join(f"@{u}" for u in s["top_accounts"])
        heading   = depth_prefix.get(s["depth"], "Sub")
        conf      = s["confidence_label"]

        print(f"{pad}└─ {heading} {label}  "
              f"({s['size']} accounts, {s['density']:.0%} density)  [{conf}]")
        print(f"{pad}     Distinctive : {dist_str}")
        print(f"{pad}     Accounts    : {sub_top}")
        print(f"{pad}     Label hint  : {s['label_hint']}")

        if s.get("subclusters"):
            print()
            _print_sub_tree(s["subclusters"], label, indent + 5)
        else:
            print()


# ── Report ────────────────────────────────────────────────────────────────────

def generate_report(export: bool = False, publish: bool = False):
    stats = get_stats()
    print(f"\nDatabase: {stats['accounts']} accounts | {stats['edges']} edges | {stats['videos']} videos")

    if stats["accounts"] < 20:
        print("\nNot enough data yet. Run at least one collection session first:")
        print("  python session.py --bootstrap")
        return

    G = build_graph()

    if G.number_of_nodes() < 10:
        print("\nGraph too small for meaningful community detection. Collect more accounts.")
        return

    # ── Pass 1: macro-level Louvain ───────────────────────────────────────────
    logger.info("Pass 1 — macro Louvain...")
    partition = community_louvain.best_partition(G, weight="weight")
    clusters_by_id: dict[int, list] = {}
    for username, cid in partition.items():
        clusters_by_id.setdefault(cid, []).append(username)

    results = []
    for cid, members in clusters_by_id.items():
        c = characterize(cid, members, G)
        results.append(c)
    results.sort(key=lambda x: x["size"], reverse=True)
    logger.info(f"Found {len(results)} raw macro-clusters")

    valid   = [c for c in results if bubble_is_valid(c)]
    invalid = [c for c in results if not bubble_is_valid(c)]

    # ── Pass 2: micro-level per valid macro-cluster ───────────────────────────
    algo_label = "Leiden" if _LEIDEN_AVAILABLE else "Louvain"
    logger.info(f"Pass 2 — sub-cluster detection ({algo_label} per macro-cluster)...")
    for c in valid:
        c["subclusters"] = detect_subclusters(c["members"], G)

    # ── Build cluster label map for cross-affinity lookup ────────────────────
    # Maps cluster_id → display label ("Cluster 1", "Cluster 2", ...)
    cluster_labels = {c["cluster_id"]: f"Cluster {i}" for i, c in enumerate(valid, 1)}

    # ── Print hierarchical report ─────────────────────────────────────────────
    print("\n" + "=" * 65)
    print("BUBBLESCAPE REPORT — Dutch TikTok")
    print(f"Two-level detection  |  sub-cluster pass: {algo_label}")
    print("=" * 65)
    print(f"\n{len(valid)} candidate bubbles | {len(invalid)} clusters below threshold\n")

    for i, c in enumerate(valid, 1):
        tag_str    = ", ".join(f"#{t}" for t in c["top_hashtags"][:5]) or "—"
        top_str    = ", ".join(f"@{u}" for u in c["top_accounts"][:4])
        bridge_str = ", ".join(f"@{u}" for u in c["top_bridges"]) or "none"

        conf_label = c.get("confidence_label", "?")
        print(f"Cluster {i}  (id={c['cluster_id']})  [{conf_label} confidence]")
        print(f"  Size             : {c['size']} accounts")
        print(f"  Total reach      : {c['total_followers']:,} followers")
        print(f"  Internal density : {c['internal_density']:.1%}")
        print(f"  Top hashtags     : {tag_str}")
        print(f"  Top accounts     : {top_str}")
        print(f"  Bridge nodes     : {bridge_str}")

        # ── Outlier / counteralgorithm ────────────────────────────────────────
        fit_scores = c.get("fit_scores", {})
        outliers = sorted(
            [(u, s) for u, s in fit_scores.items() if s < FIT_OUTLIER_THRESHOLD],
            key=lambda x: x[1],
        )[:MAX_OUTLIERS_SHOWN]

        if outliers:
            print(f"  Counter-signals  :", end="")
            first = True
            for username, score in outliers:
                affinity_label, affinity_score = find_cross_affinity(
                    username, c["cluster_id"], valid, cluster_labels, G
                )
                lean = f" → leans {affinity_label}" if affinity_label else ""
                prefix = " " if first else "                   "
                print(f"{prefix}@{username} (fit={score:.0%}){lean}")
                first = False
        else:
            # Compute average fit as a cluster cohesion signal
            if fit_scores:
                avg_fit = sum(fit_scores.values()) / len(fit_scores)
                print(f"  Cohesion         : avg fit {avg_fit:.0%} — no strong outliers")

        # Sub-cluster tree (recursive)
        subs = c.get("subclusters", [])
        if subs:
            print()
            _print_sub_tree(subs, parent_label=str(i), indent=2)
        else:
            print(f"  (no distinct sub-clusters found)")
            print()

        print(f"  ┌─ CAN YOU NAME THIS BUBBLE? ──────────────────────────────")
        print(f"  │  Look at the hashtags and top accounts.")
        print(f"  │  If you can write one sentence describing who these")
        print(f"  │  people are and what they share — it's a bubble.")
        print(f"  │  If you can't — collect more data first.")
        print(f"  └──────────────────────────────────────────────────────────")
        print()

    if invalid:
        print(f"--- {len(invalid)} clusters below threshold "
              f"(size < {MIN_BUBBLE_SIZE} or density < {MIN_INTERNAL_DENSITY:.0%}) ---")
        for c in invalid:
            print(f"  Cluster id={c['cluster_id']}: {c['size']} accounts, "
                  f"density={c['internal_density']:.1%}")

    print("\n" + "=" * 65)
    print("BUBBLE VALIDITY CRITERIA")
    print(f"  ✓ Macro: size ≥ {MIN_BUBBLE_SIZE} accounts, density ≥ {MIN_INTERNAL_DENSITY:.0%}")
    print(f"  ✓ Sub  : size ≥ {MIN_SUB_SIZE} accounts, density ≥ {MIN_SUB_DENSITY:.0%}, lift ≥ {MIN_LIFT:.1f}")
    print(f"  ✓ Nameable (manual check — the most important one)")
    print("=" * 65 + "\n")

    if export or publish:
        # Load previous graph for threshold comparison
        out_path = DB_PATH.parent / "graph.json"
        old_graph = None
        if out_path.exists():
            try:
                with open(out_path, encoding="utf-8") as f:
                    old_graph = json.load(f)
            except Exception:
                pass

        publish_changes = None
        if publish:
            # Attach fingerprints to clusters_out preview for comparison
            # (clusters_out is built inside _write_graph_json, so we compute here too)
            preview_clusters = []
            for c in valid:
                preview_clusters.append({
                    "fingerprint_id": _fingerprint_id(c["top_hashtags"]),
                    "name":           None,
                    "size":           c["size"],
                    "top_hashtags":   c["top_hashtags"],
                    "subclusters":    [{"sub_label": f"{i+1}{chr(ord('a')+j)}"}
                                       for j, s in enumerate(c.get("subclusters", []))],
                })
            _force_flag = "--force" in sys.argv
            should_publish, publish_changes = _check_publish_threshold(old_graph, preview_clusters)
            if _force_flag and not should_publish:
                publish_changes = ["Manual publish (--force)"]
                should_publish = True
            if should_publish:
                print(f"\n📌 Publishing new version — {len(publish_changes)} change(s):")
                for ch in publish_changes:
                    print(f"   • {ch}")
            else:
                print("\n⏸  No threshold crossed — skipping version publish. "
                      "(Use --force to publish anyway.)")
                publish_changes = None  # Don't write a version record

        _write_graph_json(G, valid, invalid, algo_label,
                          publish=publish,
                          publish_changes=publish_changes)


if __name__ == "__main__":
    _force   = "--force" in sys.argv   # always publish, skip threshold check
    _publish = "--publish" in sys.argv or _force
    _export  = "--export" in sys.argv or _publish
    generate_report(export=_export, publish=_publish)
