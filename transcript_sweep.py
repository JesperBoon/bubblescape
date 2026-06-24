"""
transcript_sweep.py — Whole-graph breadth sweep for transcripts.

Strategy (locked after the calibration pilot):
  * Goal: ONE transcript per account, across as many accounts as the budget allows.
    Breadth over depth — a lot of accounts with 1 transcript beats a few accounts deep.
  * Because ~37% of videos have no ASR (and a miss still costs a credit), "1 transcript
    per account" means: probe up to K videos per account until one succeeds, then move on.
  * Accounts are processed in VALUE order: hashtag-poor + high-reach first (the institutional
    accounts that are near-invisible in the hashtag graph), then everyone else by reach.
  * Politici (priority=1) already got depth from the pilot (~3.1 transcripts each) and are
    skipped here once they have a successful transcript — like every other "done" account.
  * Restricted to language-confirmed Dutch accounts so no credit is wasted on the non-Dutch
    mega-accounts that slipped through is_dutch.

Idempotent & resumable: every probe (ok / no_asr / error) is written to the `transcripts`
table immediately. An account is "done" once it has one `ok` row; videos already probed are
skipped. Re-running continues exactly where an interrupted run stopped.

Shares the fetcher, VTT parser, table and save() from the pilot — same store, no migration.

Run:  python3 transcript_sweep.py            # dry-run: print plan, spend nothing
      python3 transcript_sweep.py --commit   # the real sweep (spends credits)
"""

import sys
from collections import Counter

from collector import KonbiniClient
from db import get_conn, setup, upsert_transcript

try:
    from langdetect import detect, DetectorFactory
    DetectorFactory.seed = 0
except ImportError:
    detect = None

# ── Knobs ─────────────────────────────────────────────────────────────────────
RUN_BUDGET = 3300         # credits this sweep may spend (small buffer under ~3,316 remaining)
PROBES_PER_ACCOUNT = 3    # max videos to try per account to land its 1 transcript
MIN_ID_LEN = 18           # skip junk short ids
NL_SHARE_MIN = 0.50       # account counts as Dutch if >=50% of language-labelled videos are nl/af
HASHTAG_POOR_MAX = 0.20   # <=20% of videos tagged
MIN_VIDEOS_FOR_POOR = 5
CAPTION_NL_MIN = 0.50     # for accounts with no TikTok language labels: nl+af share of caption votes
CAPTION_SAMPLE = 10       # captions to langdetect per no-label account


def _caption_is_dutch(conn, username) -> bool:
    """Tier-3 fallback for accounts with no TikTok language field: langdetect their captions."""
    if detect is None:
        return True  # langdetect missing → don't over-filter
    caps = [r["description"] for r in conn.execute(
        "SELECT description FROM videos WHERE author=? AND description!='' "
        "ORDER BY views DESC LIMIT ?", (username, CAPTION_SAMPLE))]
    votes = Counter()
    for d in caps:
        try:
            votes[detect(d)] += 1
        except Exception:
            pass
    total = sum(votes.values())
    if total == 0:
        return False  # no signal at all → drop, to avoid non-Dutch contamination
    return (votes.get("nl", 0) + votes.get("af", 0)) / total >= CAPTION_NL_MIN


# ── Build the value-ranked account worklist ───────────────────────────────────
def build_worklist():
    """Return accounts needing a transcript, in value order (hashtag-poor+reach first)."""
    conn = get_conn()
    rows = conn.execute(f"""
        SELECT a.username, a.followers,
               COUNT(v.video_id)                                                   AS nvid,
               SUM(CASE WHEN v.hashtags NOT IN ('[]','') AND v.hashtags IS NOT NULL
                        THEN 1 ELSE 0 END)                                         AS tagged,
               SUM(CASE WHEN v.language IS NOT NULL THEN 1 ELSE 0 END)             AS lang_known,
               SUM(CASE WHEN v.language IN ('nl','af') THEN 1 ELSE 0 END)          AS nl_vids,
               MAX(CASE WHEN tr.status='ok' THEN 1 ELSE 0 END)                     AS has_ok
        FROM accounts a
        JOIN videos v        ON v.author = a.username
        LEFT JOIN transcripts tr ON tr.video_id = v.video_id
        WHERE a.is_dutch = 1 AND a.fully_collected = 1
          AND length(v.video_id) >= {MIN_ID_LEN}
        GROUP BY a.username
    """).fetchall()

    work = []
    for r in rows:
        if r["has_ok"]:                              # already has a transcript → done
            continue
        # Dutch confirmation:
        if r["lang_known"]:
            # have TikTok language labels → require majority nl/af
            if (r["nl_vids"] / r["lang_known"]) < NL_SHARE_MIN:
                continue
        else:
            # no labels → fall back to caption langdetect (catches en/es/pl mega-accounts)
            if not _caption_is_dutch(conn, r["username"]):
                continue
        poor = (r["nvid"] >= MIN_VIDEOS_FOR_POOR
                and (r["tagged"] / r["nvid"]) <= HASHTAG_POOR_MAX)
        work.append({"username": r["username"], "followers": r["followers"] or 0,
                     "poor": poor})
    conn.close()
    # value order: hashtag-poor first, then by reach
    work.sort(key=lambda a: (not a["poor"], -a["followers"]))
    return work


def candidate_videos(username):
    """Un-probed videos for an account, recent first (avoids leading with all-time viral)."""
    conn = get_conn()
    rows = conn.execute(f"""
        SELECT v.video_id
        FROM videos v
        LEFT JOIN transcripts t ON t.video_id = v.video_id
        WHERE v.author = ?
          AND length(v.video_id) >= {MIN_ID_LEN}
          AND t.video_id IS NULL
        ORDER BY (v.create_time IS NULL), v.create_time DESC, v.views ASC
        LIMIT {PROBES_PER_ACCOUNT}
    """, (username,)).fetchall()
    conn.close()
    return [r["video_id"] for r in rows]


# ── Dry-run plan ──────────────────────────────────────────────────────────────
def dry_run(work):
    HIT = 0.63                          # measured in the pilot
    PROBES_PER_ACCT = 1 / HIT           # ~1.6 expected probes to land one transcript
    reachable = int(RUN_BUDGET / PROBES_PER_ACCT)
    n_poor = sum(1 for a in work if a["poor"])
    print("=" * 64)
    print("TRANSCRIPT SWEEP — DRY RUN (no credits spent)")
    print("=" * 64)
    print(f"Accounts needing a transcript:   {len(work)}")
    print(f"  of which hashtag-poor:         {n_poor}  (processed first)")
    print(f"Run budget:                      {RUN_BUDGET} credits")
    print(f"Measured hit-rate:               {HIT*100:.0f}%  -> ~{PROBES_PER_ACCT:.1f} probes/account")
    print(f"Projected accounts covered:      ~{min(reachable, len(work))} "
          f"({min(100, reachable/max(1,len(work))*100):.0f}% of the worklist)")
    print(f"Probe cap per account:           {PROBES_PER_ACCOUNT}")
    print("\nFirst 15 accounts in processing order:")
    for a in work[:15]:
        tag = "POOR" if a["poor"] else "    "
        print(f"  [{tag}] {a['username'][:30]:30} followers={a['followers']:>10,}")
    print("=" * 64)
    print("Looks right? Re-run with --commit to start the sweep.")


# ── The real sweep ────────────────────────────────────────────────────────────
def commit(work):
    client = KonbiniClient()
    covered = 0
    accounts_done = 0
    for a in work:
        if client.credits_used >= RUN_BUDGET:
            print(f"\nRun budget reached ({client.credits_used}/{RUN_BUDGET}). Stopping.", flush=True)
            break
        vids = candidate_videos(a["username"])
        if not vids:
            continue
        accounts_done += 1
        got = False
        for vid in vids:
            if client.credits_used >= RUN_BUDGET:
                break
            res = client.get_video_transcript(vid)
            upsert_transcript(vid, res)
            if res["status"] == "ok":
                got = True
                covered += 1
                break
        mark = "✓" if got else "·"
        print(f"  {mark} {a['username'][:28]:28} "
              f"{'POOR' if a['poor'] else '    '} "
              f"covered={covered} used={client.credits_used} "
              f"rem={client.credits_remaining}", flush=True)

    print("\n" + "=" * 64)
    print(f"SWEEP DONE. accounts attempted={accounts_done}  transcripts landed={covered}  "
          f"credits used={client.credits_used}  remaining={client.credits_remaining}")
    print("=" * 64)


def main():
    global RUN_BUDGET
    if "--budget" in sys.argv:                      # optional override, e.g. --budget 40
        RUN_BUDGET = int(sys.argv[sys.argv.index("--budget") + 1])
    setup()
    work = build_worklist()
    if "--commit" in sys.argv:
        print(f"Committing sweep with budget {RUN_BUDGET} credits over {len(work)} accounts.", flush=True)
        commit(work)
    else:
        dry_run(work)


if __name__ == "__main__":
    main()
