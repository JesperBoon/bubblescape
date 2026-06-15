import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── API ──────────────────────────────────────────────────────────────────────
KONBINI_API_KEY = os.getenv("KONBINI_API_KEY")
KONBINI_BASE_URL = "https://api.konbiniapi.com/v1"

# ── Collection limits ────────────────────────────────────────────────────────
MIN_FOLLOWERS = 5000             # skip accounts below this — too small for meaningful hashtag signal
MAX_BOOTSTRAP_FOLLOWERS = 3_000_000  # skip absolute global mega-accounts — the largest legit Dutch creators go up to ~5M
MAX_FOLLOWING_PAGES = 1          # kept for reference — following lists are private, not used
MAX_VIDEO_PAGES = 2              # 2 pages × ~30 videos = ~60 videos for hashtag extraction (richer TF-IDF profiles)
MAX_TAG_PAGES = 3                # pages to fetch per seed hashtag during bootstrap
MAX_CREDITS_PER_SESSION = 500    # hard ceiling per run — change freely
#
# Credit guide (approximate, ~4 credits per fully collected account with current settings):
#   100  → key test + bootstrap only, ~10 accounts
#   250  → first sanity check, ~50 accounts
#   500  → good first real run, ~100 accounts      ← start here
#  1000  → second run once data looks right, ~220 accounts
#  2500  → serious collection session, ~580 accounts
#  5000  → major run, ~1100 accounts

# ── Dutch ecosystem filter ───────────────────────────────────────────────────
# Hard filter used in expansion: accounts with NONE of these signals are skipped.
# Scope is Dutch TikTok broadly — politics is the entry point but football,
# culture, and news are all in. The graph finds overlap; we just need the anchor.
DUTCH_SIGNALS = [
    # Unambiguous geographic markers
    "nederland", "dutch", "🇳🇱",
    "amsterdam", "rotterdam", "den haag", "utrecht", "eindhoven",
    "tilburg", "groningen", "nijmegen", "maastricht",
    # Dutch political parties / figures (specific enough not to false-match)
    "pvv", "vvd", "d66", "groenlinks", "pvda", "fvd", "boerburger",
    "wilders", "tweedekamer",
    # Dutch words (rare outside Dutch-language content)
    "politiek", "nieuws", "stemmen", "verkiezingen", "overheid", "oranje",
    # Distinctively Dutch everyday words — often appear as hashtags in NL content
    "grappig", "voorjou", "viraal", "gezellig", "lekker", "gewoon",
    "ouders", "kinderen", "school", "politie", "nederland",
    # Dutch-specific hashtags
    "tiktoknl", "nederlandsetiktok", "nederlandopstand", "stemrechts", "stemlinks",
    # Dutch media brands & broadcasters
    "telegraaf", "volkskrant", "hartvannederland",
    "nos", "rtlnieuws", "rtl4", "rtl5", "rtlboulevard", "npo", "sbs6", "vpro", "omroep", "dumpert",
    "videoland", "bright", "nrc",
    # Belgian Dutch (Flemish) — algorithmically visible in NL TikTok
    "vlaams", "belang", "vtm", "hln",
]

# ── Seed hashtags (Dutch TikTok) ─────────────────────────────────────────────
# Start broad, let hashtag_tracker.py discover culture-specific tags from real data.
# Update this list after each run based on the tracker output.
SEED_HASHTAGS = [
    # Your picks — culture/meme-aware
    "tiktoknl",
    "stemrechts",
    "stemlinks",
    "tweedekamer",
    "moslimsvoornederland",
    # Broader Dutch political
    "nederlandspolitiek",
    "nederlandsenieuws",
    "politiektiktok",
    "pvv",
    "wilders",
    "d66",
    "vvd",
    "groenlinks",
    "bbb",           # BoerBurgerBeweging
    "nsc",           # Nieuw Sociaal Contract
    "nederlandopstand",
    "nederlandsetiktok",
    # Added after first hierarchical run — right-wing / migration cluster emerging
    "fvd",
    "migratie",
    "islam",
    # Added 2026-05-31 — top new-discovery hashtags from last run (Islamic content cluster)
    "islamicreminder",
    "ice",
    "islamic",
    # Added 2026-05-31 — cultural expansion: entertainment, music, humor, lifestyle
    "missmontreal",       # Dutch pop artist, broad mainstream audience
    "lauw",               # viral meme song by Boef (Muslim rapper), youth/meme culture
    "wkvoetbal",          # WK trending now, football community outside club content
    "werk",               # people talking about work life, broad slice of NL
    "langlevedeliefde",   # popular dating/relationship show
    "firstdates",         # dating show, different audience than langlevedeliefde
    "genzteacher",        # niche: teachers on TikTok, education/social commentary
    "onderwijs",          # broader education umbrella
    "grappig",            # Dutch memes/humor, older meme culture
    "dumpert",            # humor/viral video community
    # Added 2026-06-10 — mainstream Dutch lifestyle bubbels (HypeAuditor-gebaseerd)
    "juttaleerdam",       # Dutch Olympic speed skater, massive mainstream following
    "bankzitters",        # popular Dutch comedy duo
    "joostklein",         # Dutch musician, Eurovision 2024
    "nikkietutorials",    # Dutch beauty creator, largest NL TikToker
    "schoonmaken",        # cleaning content — huge in NL (GoodFellas, etc.)
    "weekvlog",           # weekly vlog content, broad NL lifestyle slice
    "recepten",           # cooking/recipes, mainstream NL
    "afvallen",           # weight loss — very popular NL health niche
    "nederlandshumor",    # NL comedy/memes
    "temptationisland",   # popular NL reality show
    "boerzoektvrouw",     # iconic Dutch reality show, huge broad audience
    "utrechtse",          # city-based content Utrecht
    "amsterdamse",        # city-based content Amsterdam
    # Added 2026-06-14 — niches unreachable via chain (global hashtags, NL subcommunity)
    "nederlandsegamer",   # NL gaming — #gaming itself is too global for chain
    "gymcultnl",          # NL fitness/gym community — Dutch-specific tag
    "hardlopennederland", # NL running community
    "gothicnl",           # NL goth/alternative — global #goth gets blacklisted by chain
    "nederlandsecomedian",# NL standup/comedy creators
    "studentnl",          # NL student life
    "nederlandseondernemer", # NL entrepreneur content
    "hyrox",              # fitness event, huge in NL right now
    "progressiefnederland", # left-wing political (found via jesse.klaver chain)
    "boerenprotest",      # BBB/farmers movement
    "voetbalnl",          # NL football creators (not clubs, but fan creators)
]

# ── Seed accounts ────────────────────────────────────────────────────────────
# Seed accounts are the largest Dutch TikTok creators, sourced from public
# influencer-ranking sites (HypeAuditor, Modash, Marketingreport.nl), plus a
# small set of political accounts. To protect individual accounts, the actual
# handles are NOT committed to the repository: they are loaded at runtime from
# an untracked `seed_accounts.txt` (one handle per line; lines starting with '#'
# are ignored). See `seed_accounts.example.txt` for the format. If the file is
# absent, SEED_ACCOUNTS is empty and bootstrap proceeds from SEED_HASHTAGS alone.
def _load_seed_accounts():
    f = Path(__file__).parent / "seed_accounts.txt"
    if not f.exists():
        return []
    return [
        line.strip()
        for line in f.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


SEED_ACCOUNTS = _load_seed_accounts()


# ── Novelty scoring ──────────────────────────────────────────────────────────
# Accounts whose hashtag profile overlaps least with existing graph nodes
# are prioritised in the frontier queue — drives bubble diversity.
NOVELTY_HASHTAG_WEIGHT = 0.8
NOVELTY_SIZE_PENALTY = 0.2
