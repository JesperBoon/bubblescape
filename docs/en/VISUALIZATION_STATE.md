# Bubblescape — Visualization State
*Start every visualization chat with this document. For scraper/API context, see PROJECT_STATE.md.*
*For the research/paper track (critical-realist method, nested SBM, the v3 prototypes strata_view/emergence_view), see RESEARCH_NOTES.md.*

---

## What is Bubblescape?

A research tool that maps algorithmic filter bubbles on Dutch TikTok.

**Theoretical definition (use this for cluster labels and academic context):**
> A bubble is a partially closed actual domain — a structured set of empirical exposures produced by the conjoint operation of algorithmic mechanisms, social structures, and individual agency. Bubbles are emergent properties, not purely psychological (confirmation bias) nor purely structural (algorithmic determinism). They persist because the structural mechanisms producing them are largely unobservable to the people inside them.

**Technically:** bubbles = clusters in a hashtag co-occurrence graph. No follow graph (following lists are private on TikTok).

---

## Database

**Location:** `/Users/jesperboon/Documents/bubblescape/data/bubblescape.db`

**Current size (2026-06-12):**
- 4,530+ accounts total
- 2,400+ Dutch accounts (`is_dutch = 1`)
- 2,346 fully scraped (`fully_collected = 1`)
- 123,000+ videos

### Schema

```sql
accounts (
    username TEXT PRIMARY KEY,
    display_name TEXT,
    followers INTEGER,
    following_count INTEGER,
    bio TEXT,
    verified INTEGER,
    video_count INTEGER,
    hashtags TEXT,          -- JSON array: top hashtags for this account
    collected_at TEXT,
    fully_collected INTEGER, -- 1 = fully scraped
    is_dutch INTEGER        -- 1 = Dutch, 0 = non-Dutch (excluded), NULL = unknown
)

videos (
    video_id TEXT PRIMARY KEY,
    author TEXT,            -- username of the creator
    description TEXT,       -- caption (plain text)
    hashtags TEXT,          -- JSON array of hashtags in this video
    audio_id TEXT,
    audio_title TEXT,
    likes INTEGER,
    comments INTEGER,
    views INTEGER,
    collected_at TEXT
)
```

---

## How the graph should be built

### Step 1: Hashtag co-occurrence matrix

```python
# Loop over all videos of fully_collected Dutch accounts
# For each pair of hashtags in the same video → edge with weight +1
SELECT v.hashtags, a.username, a.followers
FROM videos v
JOIN accounts a ON v.author = a.username
WHERE a.is_dutch = 1 AND a.fully_collected = 1
AND v.hashtags != '[]' AND v.hashtags IS NOT NULL
```

### Step 2: Filters (crucial for cluster structure)

**Remove generic hashtags** that appear in >40% of accounts — they connect everything to everything and destroy the cluster structure:
```
fyp, foryou, viral, voorjou, foryoupage, fy, parati, trending, capcut,
voorjoupagina, fvd, pvv, fypシ, fyppppppppppppppppppppppp, tiktok
```

**Minimum threshold:** remove hashtags that appear in <3 accounts (noise).

### Step 3: Community detection

- Use **Louvain** community detection (`pip install python-louvain networkx`)
- Always recompute from the raw DB — never cache
- Expect: 8–15 meaningful clusters

### Step 4: Account → community mapping

Each account gets the community of its most-used hashtag cluster.
Accounts without enough hashtag data → "Other" group.

---

## Known clusters (based on corpus analysis)

Based on top hashtags in 123k+ videos from 2,346 Dutch accounts:

| Cluster | Core hashtags | Expected size |
|---|---|---|
| 🏛️ Politics | #politiek, #tweedekamer, #verkiezingen, #debat, #d66, #vvd, #denhaag | Large — politics is the entry point of the dataset |
| 🤣 Humor & Memes | #grappig, #humor, #bankzitters, #herkenbaar, #lachen, #comedy, #newkids | Large — mainstream NL humor |
| 🎵 Music & Joost | #joostklein, #joost, #nlrap, #muziek, #rap | Medium |
| 📰 News & Media | #nieuws, #robjetten, #podcast, #politics | Medium |
| 🏫 Education | #onderwijs, #school, #juf, #teacher | Niche but clear |
| 🌍 Cities & Regions | #amsterdam, #rotterdam, #utrecht, #denhaag | Geographically spread |
| 🕌 Islamic | #islam, #islamicreminder | Niche, clearly bounded |
| 🍳 Lifestyle & Cooking | #schoonmaken, #cleantok, #recept, #vlog | Growing via the influencer-ranking bootstrap |
| ⚽ Football | #voetbal, #football, #eredivisie, #fcutrecht, #liverpool | Problematic — partly international |
| 🏛️ History | #nostalgie, #nostalgia, #geschiedenis, #studio100 | Niche, interesting |

**Note:** `#liverpool` ranks high in the corpus (490x). This is a known contamination — Dutch football fans talking about Liverpool. Remove `liverpool`, `premier league`, `bundesliga` as stopwords in the graph build.

---

## Visualization requirements

### Must-have
- Force-directed layout (D3.js or vis.js network)
- Each **node = one account**
- **Node size** = `log10(followers)` — large accounts stand out but don't dominate
- **Node color** = community/bubble
- **Hover** → username, followers, top-3 hashtags, community label
- **Legend** with community names (automatically generated from the top hashtags per community)
- **Search bar** to highlight an account

### Nice-to-have
- Clicking a node → shows connected accounts in the sidebar
- Filter on minimum followers (slider)
- Community filter (show only bubble X)
- Export PNG

### Design
- Dark background (`#0f0f1a`)
- Vivid cluster colors (good contrast on dark)
- Minimalist, research aesthetic
- Responsive (works on a laptop screen)

---

## Output

**A single file:** `/Users/jesperboon/Documents/bubblescape/visualisatie.html`

- Fully standalone (no server needed)
- Data as inline JSON in the HTML
- Works by simply opening it in a browser

---

## Technical approach (recommended)

**Step 1: Python script** that:
1. Reads the DB and computes hashtag co-occurrence
2. Runs Louvain
3. Generates community labels (top-3 hashtags per community)
4. Exports nodes + edges as JSON

**Step 2: HTML/JS** with a D3.js force simulation
- Data as inline `<script>` JSON
- All interactivity in vanilla JS + D3

**Test first** with a subset of 200 accounts to validate the layout before rendering everything.

---

## Audience

Academic audience (thesis supervisors, researchers), but also understandable for journalists and policymakers. The visualization is primarily for exploration and presentation — not for statistical analysis.
