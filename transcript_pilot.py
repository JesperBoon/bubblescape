"""
transcript_pilot.py — Calibration pilot for the KonbiniAPI transcript endpoint.

Ran once, before the full sweep, to measure two unknowns on a small hard-capped slice of
the politici (priority=1) accounts:

  1. ASR hit-rate  — what fraction of videos actually have an auto-transcript.
  2. 404 cost      — does a "no transcript" response cost a credit, or is it free?
     (read from the X-Credits-Used / X-Credits-Remaining headers)

Result (kept for the record): hit-rate ~63% on politici, and no-ASR responses DO cost a
credit. Those two numbers set the budget math for transcript_sweep.py.

Uses the canonical pipeline: collector.KonbiniClient.get_video_transcript() and
db.upsert_transcript(), writing to the shared `transcripts` table.

Run:  python3 transcript_pilot.py
"""

import logging
from collections import Counter

from collector import KonbiniClient
from db import get_conn, setup, upsert_transcript

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

# ── Pilot knobs ───────────────────────────────────────────────────────────────
CREDIT_CAP = 150          # hard ceiling for this pilot run — never spend more
MIN_ID_LEN = 18           # skip the 3 junk short ids; real TikTok ids are 19 digits
TOTAL_BUDGET = 3466       # remaining Konbini credits — used only for the projection report


# ── Candidate selection: politici videos, highest reach first, not yet done ────
def select_politici_videos():
    conn = get_conn()
    rows = conn.execute(f"""
        SELECT v.video_id, v.author, v.views
        FROM videos v
        JOIN accounts a ON a.username = v.author
        LEFT JOIN transcripts t ON t.video_id = v.video_id
        WHERE a.priority = 1
          AND a.is_dutch = 1
          AND length(v.video_id) >= {MIN_ID_LEN}
          AND t.video_id IS NULL          -- idempotent: skip anything already probed
        ORDER BY v.views DESC
    """).fetchall()
    conn.close()
    return rows


# ── Report ────────────────────────────────────────────────────────────────────
def report(results, client):
    ok = [r for r in results if r["status"] == "ok"]
    no_asr = [r for r in results if r["status"] == "no_asr"]
    err = [r for r in results if r["status"] == "error"]
    avg = lambda xs: (sum(xs) / len(xs)) if xs else 0.0

    print("\n" + "=" * 64)
    print("TRANSCRIPT PILOT — RESULTS")
    print("=" * 64)
    print(f"Videos probed:        {len(results)}")
    print(f"  ok (transcript):    {len(ok)}")
    print(f"  no_asr (none):      {len(no_asr)}")
    print(f"  error:              {len(err)}")
    print(f"Credits used:         {client.credits_used}")
    if client.credits_remaining is not None:
        start = client.credits_remaining + client.credits_used
        print(f"Credits remaining:    {start} -> {client.credits_remaining} (delta {client.credits_used})")

    # KEY MEASUREMENT 1 — does a 'no transcript' response cost a credit?
    print("\n--- 404 / no-ASR cost ---")
    print(f"  avg credit cost of an 'ok' call:     {avg([r['credit_cost'] for r in ok]):.2f}")
    print(f"  avg credit cost of a 'no_asr' call:  {avg([r['credit_cost'] for r in no_asr]):.2f}")
    no_asr_free = avg([r["credit_cost"] for r in no_asr]) < 0.01 and len(no_asr) > 0
    if len(no_asr) == 0:
        print("  (no 'no_asr' calls observed — can't tell yet)")
    else:
        print("  => no-ASR responses are FREE." if no_asr_free else "  => no-ASR responses COST a credit.")

    # KEY MEASUREMENT 2 — ASR hit-rate
    decided = len(ok) + len(no_asr)
    hit = (len(ok) / decided) if decided else 0.0
    print("\n--- ASR hit-rate ---")
    print(f"  hit-rate (ok / (ok+no_asr)): {hit*100:.0f}%")
    if ok:
        print(f"  transcript languages: {dict(Counter(r['lang'] or '?' for r in ok))}")

    # quality sample
    print("\n--- sample transcripts ---")
    for r in ok[:3]:
        snippet = r["text"][:200].replace("\n", " ")
        print(f"  [{r['video_id']}] ({r['lang']}) {snippet}{'...' if len(r['text']) > 200 else ''}")

    # PROJECTION — how far the remaining budget reaches
    print("\n--- projection for the remaining budget ---")
    remaining_budget = TOTAL_BUDGET - client.credits_used
    UNTAGGED_POOL = 6300  # language-confirmed Dutch, hashtag-poor, untagged (measured earlier)
    if hit > 0:
        if no_asr_free:
            probes = int(remaining_budget / hit)
            print(f"  if no-ASR is FREE: ~{remaining_budget} transcripts, probing ~{probes} videos.")
        else:
            probes = remaining_budget
            print(f"  if no-ASR COSTS: ~{probes} probes -> ~{int(probes*hit)} transcripts.")
        print(f"  untagged hashtag-poor pool: ~{UNTAGGED_POOL} videos "
              f"-> covers ~{min(probes/UNTAGGED_POOL, 1.0)*100:.0f}%.")
    print("=" * 64)


def main():
    setup()
    candidates = select_politici_videos()
    print(f"Politici videos not yet probed: {len(candidates)} (cap this run: {CREDIT_CAP} credits)")
    if not candidates:
        print("Nothing to do — all politici videos already have a transcripts row.")
        return

    client = KonbiniClient()
    results = []
    for i, c in enumerate(candidates, 1):
        if client.credits_used >= CREDIT_CAP:
            print(f"Credit cap reached ({client.credits_used}/{CREDIT_CAP}). Stopping.")
            break
        res = client.get_video_transcript(c["video_id"])
        upsert_transcript(c["video_id"], res)
        results.append(res)
        mark = {"ok": "✓", "no_asr": "·", "error": "x"}[res["status"]]
        print(f"  [{i:>3}] {mark} {c['author'][:22]:22} views={c['views']:>9} "
              f"cost={res['credit_cost']} used={client.credits_used}")

    report(results, client)


if __name__ == "__main__":
    main()
