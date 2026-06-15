"""
history_append.py — append a timestamped axis snapshot to data/axis_history.json.

Called by pipeline.py after axis_scorer and pca_axes have run.
Never overwrites existing snapshots — only appends.

Format of each snapshot:
{
  "date":        ISO timestamp,
  "n_accounts":  int,
  "n_videos":    int,
  "session_note": str,        # "expansion" / "bootstrap" / custom
  "axis_scores": {...},       # from data/axis_scores.json (hand-crafted)
  "pca_scores":  {...},       # from data/pca_axes.json   (PC-based, normalised)
  "pca_meta":    {...}        # explained variance, eta², correlations
}
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE         = Path(__file__).parent
DB_PATH      = BASE / "data" / "bubblescape.db"
AXIS_JSON    = BASE / "data" / "axis_scores.json"
PCA_JSON     = BASE / "data" / "pca_axes.json"
HISTORY_PATH = BASE / "data" / "axis_history.json"


def db_stats():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM accounts WHERE fully_collected = 1")
    n_accounts = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM videos")
    n_videos = c.fetchone()[0]
    conn.close()
    return n_accounts, n_videos


def append_snapshot(session_note: str = "expansion"):
    axis_scores = json.loads(AXIS_JSON.read_text()) if AXIS_JSON.exists() else {}
    pca_data    = json.loads(PCA_JSON.read_text())  if PCA_JSON.exists()  else {}

    pca_scores = pca_data.get("cluster_pc_scores", {})
    pca_meta   = {
        "explained_variance":        pca_data.get("meta", {}).get("explained_variance", []),
        "cluster_separation_eta2":   pca_data.get("cluster_separation_eta2", {}),
        "correlations_with_handcrafted": pca_data.get("correlations_with_handcrafted", {}),
        "hashtag_loadings": {
            pc: {"positive": v["positive"][:8], "negative": v["negative"][:8]}
            for pc, v in pca_data.get("hashtag_loadings", {}).items()
        },
    }

    n_accounts, n_videos = db_stats()

    snapshot = {
        "date":         datetime.now(timezone.utc).isoformat(),
        "n_accounts":   n_accounts,
        "n_videos":     n_videos,
        "session_note": session_note,
        "axis_scores":  axis_scores,
        "pca_scores":   pca_scores,
        "pca_meta":     pca_meta,
    }

    # Load existing history or start fresh
    if HISTORY_PATH.exists():
        history = json.loads(HISTORY_PATH.read_text())
    else:
        history = {"snapshots": []}

    history["snapshots"].append(snapshot)

    HISTORY_PATH.write_text(json.dumps(history, indent=2, ensure_ascii=False))
    n = len(history["snapshots"])
    print(f"  Snapshot #{n} appended → {HISTORY_PATH.name}  ({n_accounts} accounts, {n_videos} videos)")
    return snapshot


if __name__ == "__main__":
    import sys
    note = sys.argv[1] if len(sys.argv) > 1 else "manual"
    append_snapshot(note)
