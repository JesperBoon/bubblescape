"""
pca_axes.py — data-driven axis discovery via PCA on per-account hashtag vectors.

Pipeline:
  1. Load hashtag lists per account from DB
  2. Build TF-IDF matrix (accounts × hashtags, min_df=3)
  3. Run PCA → keep top 8 components
  4. Project clusters onto PC space (mean of member accounts)
  5. Correlate PCs with hand-crafted axes from axis_scores.json
  6. Measure how well each PC separates Louvain clusters (eta² / ANOVA)
  7. Write data/pca_axes.json

Reads:
  data/bubblescape.db
  data/axis_scores.json
  bubbletree/bubbletree_data.json

Writes:
  data/pca_axes.json
"""

import json
import sqlite3
import numpy as np
from collections import defaultdict
from pathlib import Path
from scipy import stats
from sklearn.decomposition import PCA
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.preprocessing import StandardScaler

BASE       = Path(__file__).parent
DB_PATH    = BASE / "data" / "bubblescape.db"
AXIS_JSON  = BASE / "data" / "axis_scores.json"
BT_JSON    = BASE / "bubbletree" / "bubbletree_data.json"
OUT_PATH   = BASE / "data" / "pca_axes.json"

N_COMPONENTS = 8
MIN_DF = 3          # hashtag must appear in ≥ 3 accounts
MAX_FEATURES = 300  # top-300 hashtags by TF-IDF weight


# ── load data ────────────────────────────────────────────────────────────────

def load_accounts():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT username, hashtags, followers FROM accounts WHERE hashtags IS NOT NULL AND fully_collected = 1")
    rows = c.fetchall()
    conn.close()
    accounts = {}
    for username, raw, followers in rows:
        try:
            tags = json.loads(raw) if isinstance(raw, str) else raw
            tags = [t.lower().strip("#") for t in tags if t and len(t) > 1]
            if tags:
                accounts[username] = {"tags": tags, "followers": followers or 0}
        except Exception:
            pass
    return accounts


def load_cluster_membership():
    """
    Return {cluster_id: [username, ...]} from bubbletree, all depths.
    Macro clusters aggregate all their sub-cluster accounts.
    """
    bt = json.loads(BT_JSON.read_text())
    membership = defaultdict(list)

    def _walk(cluster, parent_ids):
        cid = cluster.get("id") or cluster.get("sub_label")
        if not cid:
            return
        accounts = cluster.get("accounts", [])
        for acc in accounts:
            uname = acc.get("username") or acc.get("name")
            if uname:
                membership[cid].append(uname)
                for pid in parent_ids:
                    if uname not in membership[pid]:
                        membership[pid].append(uname)
        for sub in cluster.get("subclusters", []):
            _walk(sub, parent_ids + [cid])

    for macro in bt["clusters"]:
        _walk(macro, [])

    return dict(membership)


# ── PCA ───────────────────────────────────────────────────────────────────────

def build_tfidf_matrix(accounts):
    """Return (usernames list, sparse TF-IDF matrix, feature names)."""
    usernames = list(accounts.keys())
    docs = [" ".join(accounts[u]["tags"]) for u in usernames]

    vec = TfidfVectorizer(
        min_df=MIN_DF,
        max_features=MAX_FEATURES,
        token_pattern=r"[^\s]+",   # treat each hashtag as one token
        sublinear_tf=True,
    )
    X = vec.fit_transform(docs)
    return usernames, X, vec.get_feature_names_out()


def run_pca(X, n_components=N_COMPONENTS):
    X_dense = X.toarray()
    scaler = StandardScaler(with_mean=True, with_std=False)
    X_scaled = scaler.fit_transform(X_dense)
    pca = PCA(n_components=n_components, random_state=42)
    coords = pca.fit_transform(X_scaled)
    return pca, coords


# ── cluster projections ───────────────────────────────────────────────────────

def project_clusters(usernames, coords, membership):
    """Average PC coordinates per cluster."""
    uname_to_idx = {u: i for i, u in enumerate(usernames)}
    result = {}
    for cid, members in membership.items():
        idxs = [uname_to_idx[u] for u in members if u in uname_to_idx]
        if len(idxs) < 2:
            continue
        cluster_coords = coords[idxs]
        result[cid] = {
            "mean": cluster_coords.mean(axis=0).tolist(),
            "std":  cluster_coords.std(axis=0).tolist(),
            "n":    len(idxs),
        }
    return result


# ── correlation with hand-crafted axes ───────────────────────────────────────

HANDCRAFTED = ["political_lr", "institutional_trust", "religious_identity",
               "bubble_closure", "cultural_vs_political", "reach"]

def correlate_with_handcrafted(cluster_projections, axis_scores):
    """Spearman r between each PC and each hand-crafted axis, over shared clusters."""
    shared = sorted(set(cluster_projections) & set(axis_scores))
    if len(shared) < 4:
        return {}

    results = {}
    for pc_idx in range(N_COMPONENTS):
        pc_vals = np.array([cluster_projections[c]["mean"][pc_idx] for c in shared])
        results[f"PC{pc_idx+1}"] = {}
        for ax in HANDCRAFTED:
            ax_vals = np.array([axis_scores[c].get(ax, 0.5) for c in shared])
            r, p = stats.spearmanr(pc_vals, ax_vals)
            results[f"PC{pc_idx+1}"][ax] = {"r": round(float(r), 3), "p": round(float(p), 3)}

    return results


# ── cluster separation (eta²) ─────────────────────────────────────────────────

def cluster_separation(usernames, coords, membership):
    """
    For each PC: compute eta² = SS_between / SS_total across macro clusters.
    High eta² means this PC strongly separates clusters.
    """
    macro_ids = [c for c in membership if len(c) == 1]  # "1", "2", "3"
    uname_to_idx = {u: i for i, u in enumerate(usernames)}

    # Assign each account to its macro cluster
    account_cluster = {}
    for cid in macro_ids:
        for u in membership.get(cid, []):
            account_cluster[u] = cid

    # Build groups
    groups = defaultdict(list)
    for u, cid in account_cluster.items():
        idx = uname_to_idx.get(u)
        if idx is not None:
            groups[cid].append(idx)

    if len(groups) < 2:
        return {}

    eta_sq = {}
    for pc_idx in range(N_COMPONENTS):
        all_vals = np.array([coords[i][pc_idx] for idxs in groups.values() for i in idxs])
        grand_mean = all_vals.mean()
        ss_total = ((all_vals - grand_mean) ** 2).sum()

        ss_between = 0
        for cid, idxs in groups.items():
            group_vals = np.array([coords[i][pc_idx] for i in idxs])
            n = len(group_vals)
            ss_between += n * (group_vals.mean() - grand_mean) ** 2

        eta_sq[f"PC{pc_idx+1}"] = round(float(ss_between / ss_total) if ss_total > 0 else 0, 3)

    return eta_sq


# ── top hashtags per PC ───────────────────────────────────────────────────────

def top_hashtags_per_pc(pca, feature_names, n=12):
    """For each PC, top positive and negative loading hashtags."""
    result = {}
    for i, component in enumerate(pca.components_):
        ranked = np.argsort(component)
        result[f"PC{i+1}"] = {
            "positive": [feature_names[j] for j in ranked[-n:][::-1]],
            "negative": [feature_names[j] for j in ranked[:n]],
        }
    return result


# ── local PCA per cluster ─────────────────────────────────────────────────────

def compute_local_pca(cluster_id: str, membership: dict, accounts: dict, bt_data: dict) -> dict | None:
    """
    Run PCA on just the accounts inside cluster_id.
    Find the 2 PCs with highest eta² over the sub-clusters of this cluster.
    Returns a dict with best_x, best_y, loadings, eta², or None if too small.

    This gives the "natural axes" for viewing a cluster's internal structure —
    the dimensions that best explain why its sub-clusters are different from each other.
    """
    members = membership.get(cluster_id, [])
    if len(members) < 8:
        return None

    # Find sub-clusters of this cluster from bubbletree
    sub_labels = {}  # username -> sub_id
    def _find_cluster(clusters, target_id):
        for c in clusters:
            cid = c.get("id") or c.get("sub_label")
            if cid == target_id:
                return c
            found = _find_cluster(c.get("subclusters", []), target_id)
            if found:
                return found
        return None

    parent = _find_cluster(bt_data["clusters"], cluster_id)
    if not parent:
        return None

    subs = parent.get("subclusters", [])
    if len(subs) < 2:
        return None

    for sub in subs:
        sid = sub.get("id") or sub.get("sub_label")
        for acc in sub.get("accounts", []):
            uname = acc.get("username") or acc.get("name")
            if uname:
                sub_labels[uname] = sid

    labeled = {u: sub_labels[u] for u in members if u in sub_labels and u in accounts}
    if len(labeled) < 6 or len(set(labeled.values())) < 2:
        return None

    # Build local TF-IDF
    local_users = list(labeled.keys())
    local_docs  = [" ".join(accounts[u]["tags"]) for u in local_users]
    try:
        vec = TfidfVectorizer(
            min_df=2, max_features=150,
            token_pattern=r"[^\s]+", sublinear_tf=True,
        )
        X_local = vec.fit_transform(local_docs).toarray()
        feature_names = vec.get_feature_names_out()
    except Exception:
        return None

    if X_local.shape[1] < 3:
        return None

    # Run PCA
    n_comp = min(6, X_local.shape[1], len(local_users) - 1)
    try:
        scaler = StandardScaler(with_mean=True, with_std=False)
        X_scaled = scaler.fit_transform(X_local)
        pca_local = PCA(n_components=n_comp, random_state=42)
        coords_local = pca_local.fit_transform(X_scaled)
    except Exception:
        return None

    # Compute eta² per local PC (how well does it separate sub-clusters?)
    sub_ids = [labeled[u] for u in local_users]
    unique_subs = list(set(sub_ids))
    eta_local = {}
    for pc_idx in range(n_comp):
        all_vals = coords_local[:, pc_idx]
        grand_mean = all_vals.mean()
        ss_total = ((all_vals - grand_mean) ** 2).sum()
        if ss_total == 0:
            eta_local[f"PC{pc_idx+1}"] = 0.0
            continue
        ss_between = 0
        for sid in unique_subs:
            idxs = [i for i, s in enumerate(sub_ids) if s == sid]
            group_vals = all_vals[idxs]
            ss_between += len(idxs) * (group_vals.mean() - grand_mean) ** 2
        eta_local[f"PC{pc_idx+1}"] = round(float(ss_between / ss_total), 3)

    # Best 2 PCs by eta²
    sorted_pcs = sorted(eta_local.items(), key=lambda x: -x[1])
    best_x = sorted_pcs[0][0] if len(sorted_pcs) > 0 else "PC1"
    best_y = sorted_pcs[1][0] if len(sorted_pcs) > 1 else "PC2"

    # Top hashtag loadings for best PCs
    loadings = {}
    for pc_label, _ in sorted_pcs[:4]:
        idx = int(pc_label[2:]) - 1
        if idx >= len(pca_local.components_):
            continue
        comp = pca_local.components_[idx]
        ranked = np.argsort(comp)
        loadings[pc_label] = {
            "positive": [feature_names[j] for j in ranked[-8:][::-1]],
            "negative": [feature_names[j] for j in ranked[:8]],
        }

    # Cluster positions in local PC space
    local_positions = {}
    for sid in unique_subs:
        idxs = [i for i, s in enumerate(sub_ids) if s == sid]
        sub_coords = coords_local[idxs]
        local_positions[sid] = {
            f"PC{j+1}": float(sub_coords[:, j].mean()) for j in range(n_comp)
        }

    # Normalise to 0-1
    def _norm(vals):
        mn, mx = min(vals), max(vals)
        span = mx - mn
        return [(v - mn) / span if span > 0 else 0.5 for v in vals]

    for pc_label in [f"PC{j+1}" for j in range(n_comp)]:
        raw_vals = [local_positions[s][pc_label] for s in unique_subs]
        normed   = _norm(raw_vals)
        for sid, nv in zip(unique_subs, normed):
            local_positions[sid][f"{pc_label}_norm"] = round(nv, 4)

    return {
        "cluster_id":       cluster_id,
        "n_accounts":       len(labeled),
        "n_sub_clusters":   len(unique_subs),
        "best_x":           best_x,
        "best_y":           best_y,
        "eta2":             eta_local,
        "explained_variance": [round(float(e), 4) for e in pca_local.explained_variance_ratio_],
        "loadings":         loadings,
        "sub_positions":    local_positions,
    }


# ── min-max normalise PC cluster scores to 0-1 ───────────────────────────────

def normalise_pc_scores(cluster_projections):
    """Return {cid: {PC1: 0-1, ...}} normalised across clusters."""
    cids = list(cluster_projections.keys())
    result = {c: {} for c in cids}
    for pc_idx in range(N_COMPONENTS):
        vals = np.array([cluster_projections[c]["mean"][pc_idx] for c in cids])
        mn, mx = vals.min(), vals.max()
        span = mx - mn
        for c in cids:
            raw = cluster_projections[c]["mean"][pc_idx]
            result[c][f"PC{pc_idx+1}"] = float((raw - mn) / span) if span > 0 else 0.5
            result[c][f"PC{pc_idx+1}_raw"] = float(raw)
    return result


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("Loading accounts...")
    accounts = load_accounts()
    print(f"  {len(accounts)} fully-collected accounts with hashtags")

    print("Loading cluster membership...")
    membership = load_cluster_membership()
    print(f"  {len(membership)} cluster entries")

    print("Building TF-IDF matrix...")
    usernames, X, feature_names = build_tfidf_matrix(accounts)
    print(f"  {X.shape[0]} accounts × {X.shape[1]} hashtag features")

    print("Running PCA...")
    pca, coords = run_pca(X)
    explained = pca.explained_variance_ratio_
    cumulative = np.cumsum(explained)
    print(f"  Variance explained per component:")
    for i, (e, c) in enumerate(zip(explained, cumulative)):
        print(f"    PC{i+1}: {e*100:.1f}%  (cumulative: {c*100:.1f}%)")

    print("Projecting clusters onto PC space...")
    cluster_projections = project_clusters(usernames, coords, membership)
    print(f"  {len(cluster_projections)} clusters projected")

    print("Normalising PC scores to 0-1...")
    pc_scores = normalise_pc_scores(cluster_projections)

    print("Loading hand-crafted axis scores...")
    axis_scores = json.loads(AXIS_JSON.read_text())

    print("Computing Spearman correlations with hand-crafted axes...")
    correlations = correlate_with_handcrafted(cluster_projections, axis_scores)

    print("Computing cluster separation (eta²)...")
    eta_sq = cluster_separation(usernames, coords, membership)

    print("Extracting top hashtags per PC...")
    hashtag_loadings = top_hashtags_per_pc(pca, feature_names)

    # ── print summary ───────────────────────────────────────────────────────

    print("\n" + "="*70)
    print("PCA SUMMARY")
    print("="*70)

    print("\n── Explained variance ──")
    for i in range(N_COMPONENTS):
        pc = f"PC{i+1}"
        bar = "█" * int(explained[i] * 200)
        print(f"  {pc}: {explained[i]*100:5.1f}%  {bar}")

    print("\n── Cluster separation (eta²) — how much each PC separates macro clusters ──")
    for pc, e in sorted(eta_sq.items(), key=lambda x: -x[1]):
        bar = "█" * int(e * 40)
        print(f"  {pc}: {e:.3f}  {bar}")

    print("\n── Spearman r with hand-crafted axes (macro+sub clusters) ──")
    AXES_SHORT = {
        "political_lr": "LR",
        "institutional_trust": "Trust",
        "religious_identity": "Relig",
        "bubble_closure": "Closed",
        "cultural_vs_political": "Cult/Pol",
        "reach": "Reach",
    }
    header = f"{'':6}" + "".join(f"{v:>10}" for v in AXES_SHORT.values())
    print("  " + header)
    for pc in [f"PC{i+1}" for i in range(N_COMPONENTS)]:
        row = f"  {pc:<6}"
        for ax in HANDCRAFTED:
            r = correlations.get(pc, {}).get(ax, {}).get("r", 0)
            p = correlations.get(pc, {}).get(ax, {}).get("p", 1)
            marker = "*" if p < 0.05 else " "
            row += f"{r:>+8.2f}{marker} "
        print(row)
    print("  (* p < 0.05)")

    print("\n── Top hashtags per PC ──")
    for i in range(min(4, N_COMPONENTS)):  # show top 4 PCs
        pc = f"PC{i+1}"
        pos = hashtag_loadings[pc]["positive"][:8]
        neg = hashtag_loadings[pc]["negative"][:8]
        print(f"\n  {pc} (eta²={eta_sq.get(pc,0):.3f})")
        print(f"    + (high end): {', '.join(pos)}")
        print(f"    - (low end):  {', '.join(neg)}")

    print("\n── Cluster positions on top 2 PCs ──")
    hdr = f"  {'Cluster':<8}  {'PC1':>6}  {'PC2':>6}  {'PC3':>6}  n"
    print(hdr)
    for cid in sorted(pc_scores, key=lambda x: (len(x), x)):
        if len(cid) > 2:
            continue
        row = pc_scores[cid]
        n = cluster_projections[cid]["n"]
        print(f"  {cid:<8}  {row['PC1']:>6.3f}  {row['PC2']:>6.3f}  {row['PC3']:>6.3f}  {n}")

    # ── local PCA per cluster ────────────────────────────────────────────────
    print("\nComputing local PCA per cluster (for layer-adaptive axes)...")
    bt_data = json.loads(BT_JSON.read_text())
    local_pcas = {}
    cluster_ids = [c["id"] for c in bt_data["clusters"]]
    # Also include sub-clusters that have their own sub-clusters
    def _collect_ids(clusters):
        ids = []
        for c in clusters:
            ids.append(c.get("id") or c.get("sub_label"))
            ids.extend(_collect_ids(c.get("subclusters", [])))
        return ids
    all_ids = _collect_ids(bt_data["clusters"])

    for cid in all_ids:
        result = compute_local_pca(cid, membership, accounts, bt_data)
        if result:
            local_pcas[cid] = result
            print(f"  {cid}: best axes {result['best_x']} × {result['best_y']}  "
                  f"(eta²={result['eta2'][result['best_x']]:.3f}/{result['eta2'][result['best_y']]:.3f})"
                  f"  n={result['n_accounts']}")
    print(f"  {len(local_pcas)} clusters with local PCA")

    # ── save ────────────────────────────────────────────────────────────────

    output = {
        "meta": {
            "n_accounts": len(accounts),
            "n_features": int(X.shape[1]),
            "n_components": N_COMPONENTS,
            "explained_variance": [round(float(e), 4) for e in explained],
            "cumulative_variance": [round(float(c), 4) for c in cumulative],
        },
        "cluster_separation_eta2": eta_sq,
        "correlations_with_handcrafted": correlations,
        "hashtag_loadings": hashtag_loadings,
        "cluster_pc_scores": pc_scores,
        "cluster_pc_raw": {
            cid: {f"PC{j+1}": v["mean"][j] for j in range(N_COMPONENTS)}
            for cid, v in cluster_projections.items()
        },
        "local_pca": local_pcas,
    }

    OUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\nWrote results to {OUT_PATH}")


if __name__ == "__main__":
    main()
