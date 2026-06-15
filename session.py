"""
session.py — Session runner. Orchestrates one collection run.

Usage:
    python3 session.py              # expand frontier (needs existing data)
    python3 session.py --bootstrap  # seed from hashtags + seed accounts, then expand
    python3 session.py --health     # test API key only, no collection
"""

import sys
import uuid
import json
import logging
import re
from collections import Counter
from datetime import datetime

from config import (
    MIN_FOLLOWERS,
    MAX_BOOTSTRAP_FOLLOWERS,
    MAX_VIDEO_PAGES,
    MAX_TAG_PAGES,
    MAX_CREDITS_PER_SESSION,
    SEED_HASHTAGS,
    SEED_ACCOUNTS,
    DUTCH_SIGNALS,
)
from collector import KonbiniClient
from db import (
    setup, upsert_account, upsert_video,
    get_frontier_usernames, get_frontier_scored, get_frontier_with_signal_count,
    mark_account_collected, mark_non_dutch, account_exists, log_session, get_stats,
)

# Chain discovery budget: fraction of MAX_CREDITS_PER_SESSION for in-session chained searches
CHAIN_DISCOVERY_BUDGET_FRAC = 0.15
# Minimum accounts found per search to consider a chain signal "productive"
CHAIN_MIN_NEW_ACCOUNTS = 2
# A signal is "already dominant" if this many Dutch collected accounts share it
CHAIN_DOMINANCE_THRESHOLD = 30

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _top_hashtags(videos: list, n: int = 20) -> list:
    counts = Counter()
    for v in videos:
        counts.update(v.get("hashtags", []))
    return [tag for tag, _ in counts.most_common(n)]


def is_dutch_account(
    profile: dict,
    hashtags: list,
    video_descriptions: list[str] | None = None,
    video_languages: list[str | None] | None = None,
) -> bool:
    """
    Dutch ecosystem filter. Four-tier check, each tier only runs if previous fails:

    0. TikTok language signal: if we have language codes from videos (new field,
       populated going forward), use them directly — ≥20% "nl" → accept.
       Falls back to lower tiers if no language data available (NULL for old videos).

    1. Bio + username: substring match against DUTCH_SIGNALS

    2. Hashtags: exact match against DUTCH_SIGNALS

    3. Video descriptions: langdetect language check — if ≥40% of sampled
       descriptions are Dutch, accept. Catches institutional accounts (NOS, RTL,
       FvD, SBS6) that don't use hashtags but post Dutch-language content.
    """
    # Tier 0: TikTok's own language detection (only available for newly scraped videos)
    # video_languages is a list of language codes or None per video
    if video_languages:
        known = [lang for lang in video_languages if lang is not None]
        if known:
            nl_frac = known.count("nl") / len(known)
            if nl_frac >= 0.2:
                return True
            # If we have strong signal of non-Dutch (>80% non-nl), skip lower tiers
            # to avoid langdetect false-positives on Dutch accounts posting in English
            if nl_frac == 0.0 and len(known) >= 3:
                # Still check bio/hashtags — an English-language Dutch account
                # (e.g. a large mainstream creator) should not be rejected on language alone
                pass  # fall through to bio/hashtag checks below

    bio = (profile.get("bio") or "").lower()
    username = (profile.get("username") or "").lower()
    bio_username_text = bio + " " + username

    if any(signal in bio_username_text for signal in DUTCH_SIGNALS):
        return True

    hashtag_set = {h.lower() for h in hashtags}
    if any(signal in hashtag_set for signal in DUTCH_SIGNALS):
        return True

    # Tier 2b: #words embedded in description text (API sometimes omits tag field)
    if video_descriptions:
        desc_tags = set()
        for d in video_descriptions:
            desc_tags.update(t.lower() for t in re.findall(r'#(\w+)', d))
        if any(signal in desc_tags for signal in DUTCH_SIGNALS):
            return True

    # Tier 3: langdetect fallback — only used when no TikTok language data available
    if video_descriptions and not (video_languages and any(l is not None for l in video_languages)):
        try:
            from langdetect import detect, LangDetectException, DetectorFactory
            DetectorFactory.seed = 0
            votes = [detect(d) for d in video_descriptions[:5] if d]
            nl_frac = votes.count("nl") / len(votes) if votes else 0
            if nl_frac >= 0.2:
                return True
        except Exception:
            pass

    return False


# Known non-Dutch suffixes/keywords in usernames — quick reject without API call
_GLOBAL_REJECT_SIGNALS = [
    "usa", "uk", "espn", "nfl", "nba", "nhl", "mlb", "nascar",
    "redbull", "skysports", "talksport", "cbs", "nbc", "abc",
    "premierleague", "bundesliga", "seriea", "ligue1",
]

def _is_obviously_not_dutch(username: str, display_name: str = "") -> bool:
    """Fast username/display_name check — rejects obvious global accounts before they enter the frontier."""
    text = (username + " " + display_name).lower()
    if any(sig in text for sig in _GLOBAL_REJECT_SIGNALS):
        return True
    # Accounts with clearly English-only brand names combined with high-follower sports content
    # are caught by the Dutch filter at collection time anyway — this is just a fast pre-screen
    return False


# ── Chain discovery helpers ───────────────────────────────────────────────────

def _extract_mentions(videos: list) -> list[str]:
    """Extract @mentioned usernames from video descriptions."""
    mentions = Counter()
    for v in videos:
        desc = v.get("description", "") or ""
        for m in re.findall(r'@([\w.]+)', desc):
            if len(m) > 2:
                mentions[m.lower()] += 1
    # Return usernames mentioned at least twice (reduces noise)
    return [u for u, cnt in mentions.most_common(10) if cnt >= 2]


def _corpus_hashtag_counts() -> Counter:
    """Build hashtag frequency counts from all fully-collected Dutch accounts."""
    import sqlite3
    from db import get_conn
    conn = get_conn()
    rows = conn.execute(
        "SELECT hashtags FROM accounts WHERE fully_collected = 1 AND is_dutch = 1"
    ).fetchall()
    conn.close()
    counts: Counter = Counter()
    for r in rows:
        counts.update(json.loads(r[0]))
    return counts


def _chain_signals_from_account(all_videos: list, corpus_counts: Counter) -> tuple[list[str], list[str]]:
    """
    Extract novel hashtags and audio IDs from a just-collected account's videos.

    Hashtag novelty uses TF-IDF-style scoring:
        score = account_freq / (corpus_freq + 1)
    High score = this account uses this tag much more than the corpus average
    → strongly characteristic of this account's niche, not generic.

    Hard filters applied first:
    - Skip tags already dominant in corpus (>= CHAIN_DOMINANCE_THRESHOLD) — too generic
    - Skip tags used only once by this account — likely noise

    Returns (novel_hashtags, audio_ids).
    """
    tag_counts: Counter = Counter()
    audio_ids: list[str] = []
    for v in all_videos:
        tag_counts.update(v.get("hashtags", []))
        aid = v.get("audio_id", "")
        if aid:
            audio_ids.append(aid)

    # Score each tag by how characteristic it is for this account vs. corpus
    scored = []
    for tag, account_freq in tag_counts.items():
        corpus_freq = corpus_counts.get(tag, 0)
        if corpus_freq >= CHAIN_DOMINANCE_THRESHOLD:
            continue  # too generic — skip
        if account_freq < 2:
            continue  # used only once — likely noise
        score = account_freq / (corpus_freq + 1)
        scored.append((tag, score))

    scored.sort(key=lambda x: x[1], reverse=True)
    novel_tags = [tag for tag, _ in scored[:3]]

    # Deduplicate audio IDs, keep top-3
    seen = set()
    unique_audio = []
    for a in audio_ids:
        if a not in seen:
            seen.add(a)
            unique_audio.append(a)
        if len(unique_audio) >= 3:
            break

    return novel_tags, unique_audio


CHAIN_MAX_NEW_ACCOUNTS = 3  # probe limit — just enough to check if chain continues

CHAIN_MAX_NEW_ACCOUNTS = 3  # probe limit — just enough to check if chain continues

def _chain_search(client: KonbiniClient, signal: str) -> int:
    """
    Probe one signal: fetch one page, check the first valid result for Dutch signals.
    If the first author looks non-Dutch, abandon immediately — wrong territory.
    Otherwise add up to CHAIN_MAX_NEW_ACCOUNTS new accounts.
    Returns number of new accounts added, or -1 if abandoned as non-Dutch.
    """
    new_found = 0
    dutch_confirmed = False

    for page in client.search_videos(signal, max_pages=1):
        for entry in page:
            author = entry["author"]
            if not author["username"]:
                continue
            followers = author.get("followers", 0)
            if followers > MAX_BOOTSTRAP_FOLLOWERS:
                continue
            if followers > 0 and followers < MIN_FOLLOWERS:
                continue
            if _is_obviously_not_dutch(author["username"], author.get("display_name", "")):
                continue

            # First valid author is the quality gate
            if not dutch_confirmed:
                bio = author.get("bio", "") or ""
                quick_text = author["username"].lower() + " " + author.get("display_name", "").lower() + " " + bio.lower()
                if not any(sig in quick_text for sig in DUTCH_SIGNALS):
                    logger.debug(f"  Chain abandon #{signal}: first author @{author['username']} has no Dutch signal")
                    return -1  # wrong territory, stop immediately
                dutch_confirmed = True

            if new_found >= CHAIN_MAX_NEW_ACCOUNTS:
                break
            if not account_exists(author["username"]):
                upsert_account({
                    "username": author["username"],
                    "display_name": author.get("display_name", ""),
                    "followers": followers,
                    "hashtags": [],
                })
                new_found += 1
        break
    return new_found


# ── Rolling discovery: mini-search on every expansion run ────────────────────

def _rolling_discovery(client: KonbiniClient):
    """
    Every expansion run searches a rotating subset of SEED_HASHTAGS so the
    frontier never runs empty between bootstraps.

    Uses ~30 credits (3 hashtags × 1 page × ~10 results). Rotates through
    the full seed list across runs so all hashtags get coverage over time.
    Skips accounts already in the DB.
    """
    import random
    tags = random.sample(SEED_HASHTAGS, min(3, len(SEED_HASHTAGS)))
    logger.info(f"Rolling discovery: searching #{', #'.join(tags)}")
    new_found = 0
    for tag in tags:
        for page in client.search_videos(tag, max_pages=1):
            for entry in page:
                author = entry["author"]
                if not author["username"]:
                    continue
                followers = author.get("followers", 0)
                if followers > MAX_BOOTSTRAP_FOLLOWERS:
                    continue
                if followers > 0 and followers < MIN_FOLLOWERS:
                    continue
                if _is_obviously_not_dutch(author["username"], author.get("display_name", "")):
                    continue
                if not account_exists(author["username"]):
                    upsert_account({
                        "username": author["username"],
                        "display_name": author.get("display_name", ""),
                        "followers": followers,
                        "hashtags": [],
                    })
                    new_found += 1
    logger.info(f"Rolling discovery: {new_found} new accounts added to frontier")


# ── Bootstrap: seed the DB from search + seed accounts ───────────────────────

def bootstrap(client: KonbiniClient):
    """
    Phase 1: search for videos by hashtag keyword to discover creators.
             Uses search/videos (reliable) instead of tags/{tag}/videos (broken).
    Phase 2: directly collect the known seed accounts (full profile + videos + following).
    """
    logger.info("=== BOOTSTRAP: discovering accounts via search ===")
    for tag in SEED_HASHTAGS:
        if client.credits_used >= MAX_CREDITS_PER_SESSION:
            break
        logger.info(f"  searching: #{tag}")
        for page in client.search_videos(tag, MAX_TAG_PAGES):
            for entry in page:
                author = entry["author"]
                if not author["username"]:
                    continue
                if author.get("followers", 0) > MAX_BOOTSTRAP_FOLLOWERS:
                    continue  # skip global mega-accounts (ESPN, etc.)
                if not account_exists(author["username"]):
                    upsert_account({
                        "username": author["username"],
                        "display_name": author.get("display_name", ""),
                        "followers": author.get("followers", 0),
                        "hashtags": [],  # left empty until full collection
                    })
                upsert_video(entry["video"])
        logger.info(f"    credits used so far: {client.credits_used}")

    logger.info("=== BOOTSTRAP: collecting seed accounts directly ===")
    for username in SEED_ACCOUNTS:
        if client.credits_used >= MAX_CREDITS_PER_SESSION:
            break
        logger.info(f"  @{username}")
        collect_account(client, username)  # return values not used during bootstrap

    stats = get_stats()
    logger.info(f"Bootstrap complete: {stats['accounts']} accounts, {stats['videos']} videos")


# ── Collect a single account ──────────────────────────────────────────────────

def collect_account(client: KonbiniClient, username: str, known_followers: int = None) -> tuple[bool, list]:
    """
    Full collection for one account:
      1. Profile (1 credit)
      2. Videos up to MAX_VIDEO_PAGES (extract hashtags)

    Returns (collected: bool, videos: list).
    - collected=True means account passed Dutch filter and was stored.
    - videos is non-empty only when collected=True (used for chain discovery).
    known_followers: if already in DB, skip the profile fetch for clear under-threshold accounts.
    """
    # Pre-check: skip API call entirely if DB already shows < MIN_FOLLOWERS
    # Only skip if we have a real follower count (>0) — unknown (0) still needs a profile fetch
    if known_followers is not None and known_followers > 0 and known_followers < MIN_FOLLOWERS:
        logger.debug(f"  Skip {username}: {known_followers} followers in DB < {MIN_FOLLOWERS} (no API call)")
        mark_account_collected(username)
        return False, []

    # 1. Profile
    profile = client.get_user_profile(username)
    if not profile:
        logger.debug(f"  Could not fetch profile for {username}")
        return False, []

    if profile["followers"] < MIN_FOLLOWERS:
        logger.debug(f"  Skip {username}: {profile['followers']} followers < {MIN_FOLLOWERS}")
        upsert_account({**profile, "hashtags": []})
        mark_account_collected(username)  # don't retry low-follower accounts
        return False, []

    # 2. Videos — early exit after page 1 if not Dutch (saves 1 credit per non-Dutch account)
    all_videos = []
    pages = list(client.get_user_videos(username, 1))  # fetch page 1 only first
    for page in pages:
        all_videos.extend(page)
        for v in page:
            upsert_video(v)

    profile["hashtags"] = _top_hashtags(all_videos)
    descriptions = [v.get("description", "") for v in all_videos if v.get("description")]
    languages = [v.get("language") for v in all_videos]  # may contain None for old/missing data

    if not is_dutch_account(profile, profile["hashtags"], video_descriptions=descriptions, video_languages=languages):
        mark_non_dutch(username)
        logger.debug(f"  Skip {username}: no Dutch signal")
        return False, []

    # Dutch confirmed — fetch remaining video pages for richer hashtag data
    if MAX_VIDEO_PAGES > 1:
        for page in client.get_user_videos(username, MAX_VIDEO_PAGES - 1):
            all_videos.extend(page)
            for v in page:
                upsert_video(v)
        profile["hashtags"] = _top_hashtags(all_videos)

    upsert_account(profile)
    mark_account_collected(username)
    logger.info(
        f"  {username:30s}  "
        f"followers={profile['followers']:>7,}  "
        f"videos={len(all_videos):>3}  "
        f"tags={profile['hashtags'][:3]}"
    )

    return True, all_videos


# ── Main session ──────────────────────────────────────────────────────────────

def run_session(do_bootstrap: bool = False):
    setup()
    client = KonbiniClient()
    session_id = str(uuid.uuid4())[:8]
    started_at = datetime.utcnow().isoformat()
    accounts_collected = 0

    logger.info(f"{'='*50}")
    logger.info(f"Session {session_id} started")
    logger.info(f"Credit limit this session: {MAX_CREDITS_PER_SESSION}")
    logger.info(f"{'='*50}")

    if do_bootstrap:
        bootstrap(client)

    # Auto-bootstrap if frontier is too thin — fewer than 100 accounts with real hashtag
    # signal means chain discovery has dried up and we need fresh seed accounts.
    # Costs ~60-80 credits but fills the frontier with quality accounts.
    AUTO_BOOTSTRAP_THRESHOLD = 100
    frontier_with_signal = get_frontier_with_signal_count()
    if not do_bootstrap and frontier_with_signal < AUTO_BOOTSTRAP_THRESHOLD:
        logger.info(f"Frontier thin ({frontier_with_signal} accounts with hashtag signal < {AUTO_BOOTSTRAP_THRESHOLD}) — auto-bootstrapping")
        bootstrap(client)

    # Build frontier: scored by log(followers) × hashtag novelty vs. corpus
    # The queue is refreshed every FRONTIER_REFRESH_EVERY accounts so that:
    #   1. newly chain-discovered accounts enter the ranking mid-run
    #   2. novelty scores stay accurate as the corpus grows during a long run
    FRONTIER_REFRESH_EVERY = 50

    queue = get_frontier_scored(limit=500)
    seen: set[str] = set()  # avoid re-processing accounts across refreshes
    logger.info(f"Frontier: {len(queue)} accounts queued (novelty-scored)")

    chain_budget = int(MAX_CREDITS_PER_SESSION * CHAIN_DISCOVERY_BUDGET_FRAC)
    chain_credits_used = 0
    corpus_counts: Counter | None = None
    consecutive_chain_misses = 0
    chain_blacklist: set[str] = set()  # signals abandoned as non-Dutch this session
    accounts_since_refresh = 0

    for username, known_followers in queue:
        # Skip already-processed accounts (can appear after a refresh)
        if username in seen:
            continue
        if client.credits_used >= MAX_CREDITS_PER_SESSION:
            logger.info(f"Credit limit reached ({client.credits_used}/{MAX_CREDITS_PER_SESSION})")
            break

        seen.add(username)
        credits_before = client.credits_used
        collected, videos = collect_account(client, username, known_followers=known_followers)
        if collected:
            accounts_collected += 1
            accounts_since_refresh += 1

            # Refresh frontier every N accounts so new chain-discovered accounts
            # enter the ranking and novelty scores stay accurate mid-run
            if accounts_since_refresh >= FRONTIER_REFRESH_EVERY:
                queue = get_frontier_scored(limit=500)
                accounts_since_refresh = 0
                corpus_counts = None  # also invalidate corpus so chain scoring stays fresh
                logger.info(f"  Frontier refreshed mid-run ({accounts_collected} accounts so far)")

            # ── Chain discovery ───────────────────────────────────────────────
            if chain_credits_used < chain_budget and videos:
                if corpus_counts is None:
                    corpus_counts = _corpus_hashtag_counts()

                novel_tags, _audio_ids = _chain_signals_from_account(videos, corpus_counts)

                # Also add @mentions as direct frontier candidates
                # @mentions: add with followers=0 (unknown) — will be checked at collection time
                mentions = _extract_mentions(videos)
                for mentioned in mentions:
                    if not account_exists(mentioned):
                        upsert_account({"username": mentioned, "display_name": "", "followers": 0, "hashtags": []})

                # Filter out blacklisted signals before trying
                valid_tags = [t for t in novel_tags if t not in chain_blacklist]

                if valid_tags:
                    tag = valid_tags[0]
                    credits_before_chain = client.credits_used
                    new_accounts = _chain_search(client, tag)
                    chain_credits_used += client.credits_used - credits_before_chain

                    if new_accounts >= CHAIN_MIN_NEW_ACCOUNTS:
                        logger.info(f"  Chain discovery: #{tag} → {new_accounts} new accounts (budget used: {chain_credits_used}/{chain_budget})")
                        consecutive_chain_misses = 0
                        corpus_counts = None
                    elif new_accounts == -1:
                        # Non-Dutch territory — blacklist and try next tag
                        chain_blacklist.add(tag)
                        logger.debug(f"  Chain abandoned #{tag}: non-Dutch territory (blacklisted for session)")
                        for fallback_tag in valid_tags[1:]:
                            if fallback_tag in chain_blacklist:
                                continue
                            credits_before_chain = client.credits_used
                            new_accounts = _chain_search(client, fallback_tag)
                            chain_credits_used += client.credits_used - credits_before_chain
                            if new_accounts == -1:
                                chain_blacklist.add(fallback_tag)
                            elif new_accounts >= CHAIN_MIN_NEW_ACCOUNTS:
                                logger.info(f"  Chain discovery: #{fallback_tag} → {new_accounts} new accounts (budget used: {chain_credits_used}/{chain_budget})")
                                corpus_counts = None
                                break
                            else:
                                break
                    else:
                        consecutive_chain_misses += 1
                        logger.debug(f"  Chain miss on #{tag}: only {new_accounts} new accounts (miss #{consecutive_chain_misses})")

                        if consecutive_chain_misses >= 3:
                            logger.info("  Chain discovery: 3 consecutive misses — falling back to rolling seed search")
                            if chain_credits_used < chain_budget:
                                credits_before_fallback = client.credits_used
                                _rolling_discovery(client)
                                chain_credits_used += client.credits_used - credits_before_fallback
                            consecutive_chain_misses = 0
                else:
                    logger.debug(f"  Chain discovery: no novel signals from @{username}")

        spent = client.credits_used - credits_before
        logger.debug(f"  Credits this account: {spent}, total: {client.credits_used}")

    # Finalise
    finished_at = datetime.utcnow().isoformat()
    log_session(session_id, started_at, finished_at, client.credits_used, accounts_collected)

    stats = get_stats()
    logger.info(f"{'='*50}")
    logger.info(f"Session {session_id} done")
    logger.info(f"  Accounts collected : {accounts_collected}")
    logger.info(f"  Credits used       : {client.credits_used}")
    if client.credits_remaining is not None:
        logger.info(f"  Credits remaining  : {client.credits_remaining}")
    logger.info(f"  DB totals          : {stats['accounts']} accounts | {stats['edges']} edges | {stats['videos']} videos")
    logger.info(f"{'='*50}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--health" in sys.argv:
        client = KonbiniClient()
        ok = client.health_check()
        sys.exit(0 if ok else 1)

    do_bootstrap = "--bootstrap" in sys.argv
    run_session(do_bootstrap=do_bootstrap)
