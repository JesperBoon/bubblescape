"""
pipeline.py — Full Bubblescape run: collect → track hashtags → track sounds → analyze.

Usage:
    python3 pipeline.py               # expansion run (needs existing data)
    python3 pipeline.py --bootstrap   # fresh start from seed hashtags + accounts

The pipeline runs all phases in sequence. After each run, review the
hashtag tracker, sound tracker, and bubble report, then decide:
  - Which NEW-DISCOVERY hashtags to add to SEED_HASHTAGS in config.py
  - Whether any sounds are becoming bubble signals
  - Whether clusters are nameable (if yes, you have real bubbles)
  - Whether to run another expansion session or re-bootstrap with new seeds

That review step is intentionally manual — it's where your judgment lives.
"""

import sys
from db import setup
from session import run_session
from hashtag_tracker import generate_report as hashtag_report
from sound_tracker import generate_report as sound_report
from analyze import generate_report as bubble_report
from generate_viz import main as build_viz
from name_clusters import run as name_clusters
import axis_scorer
import pca_axes
import strategy
from history_append import append_snapshot


def run_pipeline(bootstrap: bool = False):
    _divider()
    print("BUBBLESCAPE PIPELINE")
    if bootstrap:
        print("Mode: BOOTSTRAP (fresh discovery from seed hashtags)")
    else:
        print("Mode: EXPANSION (deepening existing frontier)")
    _divider()

    # 1. Collection
    print("\n▶ STEP 1 — COLLECTION\n")
    setup()
    run_session(do_bootstrap=bootstrap)

    # 2. Hashtag tracker
    print("\n▶ STEP 2 — HASHTAG TRACKER\n")
    hashtag_report(save=True)

    # 3. Sound tracker
    print("\n▶ STEP 3 — SOUND TRACKER\n")
    sound_report(save=True)

    # 4. Bubble analysis + JSON export
    print("\n▶ STEP 4 — BUBBLE ANALYSIS\n")
    publish = "--publish" in sys.argv
    bubble_report(export=True, publish=publish)

    # 5. Auto-name clusters via Claude API (only if ANTHROPIC_API_KEY is set)
    import os; from dotenv import load_dotenv; load_dotenv()
    if os.getenv("ANTHROPIC_API_KEY"):
        print("\n▶ STEP 5 — AUTO-NAMING CLUSTERS (Claude API)\n")
        name_clusters(force=False, dry_run=False)
    else:
        print("\n▶ STEP 5 — AUTO-NAMING SKIPPED (no ANTHROPIC_API_KEY in .env)\n")

    # 6. Regenerate visualization
    print("\n▶ STEP 6 — VISUALIZATION\n")
    build_viz()

    # 7. Axis scores (hand-crafted)
    print("\n▶ STEP 7 — AXIS SCORES (hand-crafted)\n")
    axis_scorer.main()

    # 8. PCA axes (data-driven)
    print("\n▶ STEP 8 — PCA AXES (data-driven)\n")
    pca_axes.main()

    # 9. Append history snapshot
    print("\n▶ STEP 9 — HISTORY SNAPSHOT\n")
    session_note = "bootstrap" if bootstrap else "expansion"
    append_snapshot(session_note)

    # 10. Strategy report — seeds for next run
    print("\n▶ STEP 10 — STRATEGY (seeds voor volgende run)\n")
    strategy.main()

    # 10. What to do next
    _divider()
    print("PIPELINE COMPLETE — what to do now:\n")
    print("  1. Review NEW-DISCOVERY hashtags — add promising ones to SEED_HASHTAGS in config.py")
    print("  2. Review sounds — original sounds in 5+ accounts are bubble signals worth noting")
    print("  3. Look at the bubble clusters — can you name each one in one sentence?")
    print("     Yes → real bubble. No → collect more and run again.")
    print()
    print("  Next run:")
    print("     python3 pipeline.py              # expand frontier (no version bump)")
    print("     python3 pipeline.py --publish    # expand + publish new version if threshold crossed")
    print("     python3 pipeline.py --bootstrap  # re-bootstrap with updated seeds")
    print("     python3 name_clusters.py --all   # force re-name all clusters via Claude API")
    _divider()


def _divider():
    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    bootstrap = "--bootstrap" in sys.argv
    run_pipeline(bootstrap=bootstrap)
