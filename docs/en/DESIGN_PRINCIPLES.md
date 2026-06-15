# Bubblescape — Design Principles & Wishlist

Compiled from all the conversations. This is what the project wants.
Use this document at the start of every new session.

---

## 1. What is Bubblescape really?

Bubblescape is a research tool that maps algorithmic filter bubbles on Dutch TikTok. It scrapes accounts, builds a hashtag-based graph, detects communities via Louvain, and visualizes them as an interactive bubble map.

It is not a static dashboard. It is a living system that can reveal new structure with every scrape session. Bubbles split, shift, disappear — that is the point.

The research goal: to understand what information bubbles on TikTok look like, what distinguishes them from each other, and whether they can be named as coherent communities.

---

## 2. Data & Scraping

### What we scrape
- TikTok accounts: username, display name, bio, follower count, hashtags from recent videos
- Videos: hashtags, sounds, language, date — for TF-IDF hashtag profiles
- We do NOT scrape subtitles or transcriptions (KonbiniAPI does not offer this)

### How many hashtags per account
- **MAX_VIDEO_PAGES = 2** — about 60 videos per account
- This was 1 page (30 videos). More pages = richer TF-IDF profiles = better clustering
- More pages cost more credits but it is worth it for quality

### Efficiency
- **Accounts with <500 followers are filtered before the API call** if that is already known in the DB
- This saves ~274 credits per run (it was wasteful: first fetch a 1-credit profile, then throw it away)
- `get_frontier_usernames()` returns `(username, known_followers)` so we can pre-check

### Strategic scraping
- Don't blindly follow the frontier — that gives arbitrary expansion
- After each run, `strategy.py` generates a report with:
  - **Bridge accounts**: sit between clusters, lead to undiscovered communities
  - **Outlier accounts**: low fit score in their cluster, possibly the start of a new bubble
  - **Axis coverage gaps**: dimensions with little spread, hashtag seeds to improve them
  - **Unseen hashtags**: tags that appear among bridges/outliers but are not yet in SEED_HASHTAGS
- The researcher decides which seeds are promising — that judgment is deliberately manual

### Bootstrap vs. expansion
- `--bootstrap`: fresh start from SEED_HASHTAGS and SEED_ACCOUNTS in config.py
- no flag: expand the frontier from existing data
- After a bootstrap the Louvain clusters often change composition

---

## 3. Clustering & Bubbles

### Emergent structure
- **No fixed depth limit.** Louvain may go as deep as the data justifies
- Stopping criteria are data-driven:
  - `MIN_VARIANCE_RATIO = 0.10`: a split must explain ≥10% of the hashtag entropy
  - `MIN_SPLIT_CONFIDENCE = 0.05`: sub-clusters must have enough internal cohesion
  - `LIFT_DEPTH_INCREMENT = 0.8`: the lift threshold rises per layer, so splits stop on their own
  - `MAX_DETECTION_DEPTH = 8`: only a safety limit, not a functional one
- A cluster with little internal distinction simply does not split

### Stability across runs
- Louvain is non-deterministic — after a new run clusters can change composition
- **Fingerprint matching**: clusters are identified by their top-5 hashtags (sorted, comma-separated)
- Names and descriptions are stored in `cluster_names.json` tied to that fingerprint
- If a fingerprint changes (the cluster has been significantly reshuffled) the cluster gets a new name via Claude

### Never fixed clusters
- Bubbles are hypotheses, not facts. They must always be able to change
- New data can lead to: splitting an existing bubble, merging, shifting
- The visualization must reflect this — no "these are THE three bubbles"

---

## 4. Names — Clusters and Axes

This is a hard principle: **nowhere in the tool do hashtags appear as a name or label.**

### Cluster names
- All macro-clusters and sub-clusters get a descriptive name via `name_clusters.py` (Claude API)
- The name describes the *community identity*: who these people are, what connects them
- **Never**: "fyp, viral, nederland" as a name. **Instead**: "Ordinary Dutch TikTok" or "Dutch Muslim Comedy Creators"
- Prompt instruction to Claude: no hashtags in the name, no hashtag-like words
- Sub-clusters at every depth are named

### Axis names
- All PCA axes get a name via `name_axes.py` (Claude API):
  - `name`: the name of the axis (max 5 words, Dutch)
  - `rationale`: 1–2 sentences on what the axis measures and why accounts score high/low
  - `high_label`: label for the high end (max 4 words)
  - `low_label`: label for the low end (max 4 words)
- If Claude has not run yet: use correlation with hand-crafted axes as fallback (r ≥ 0.65)
- If that is also unavailable: show `PC5 (not yet named)` — an honest placeholder, never hashtags
- **Pole labels on the plot**: always `high_label`/`low_label`, never the top hashtags from the loadings
- **Axis pills in the topbar**: always the name, never "PC5 · vvd → fyp"

### Hand-crafted axes (existing system)
- `axis_scorer.py` computes scores for theoretical axes: Left–Right, Institutional trust, Religious identity, Cultural vs. political, Bubble closure, Reach
- These are interpretively the strongest but manually defined
- Correlations between PCA axes and hand-crafted axes are *context*, not the name itself

---

## 5. Axes — Behavior & Philosophy

### Interpretively interesting
- The best axis is not the one with the most global variance (standard PCA)
- The best axis is the one that *distinguishes* clusters most — measured in eta² (ANOVA over cluster membership)
- This is more interpretively interesting: it shows which dimensions are responsible for the separation between bubbles

### Layer-adaptive axes
- At the **macro level**: the 2 global PCs with the highest eta² over the macro-clusters
- After **zooming into a cluster**: local PCA on only the accounts in that cluster
  - New TF-IDF matrix on that subset
  - Its own PCs computed
  - The 2 local PCs with the highest eta² over the sub-clusters are chosen automatically
  - Sub-cluster positions stored as normalized values (0–1) in `pca_axes.json` under `local_pca[cluster_id]`
- The axes therefore change *every time you go one layer deeper*
- This shows: "what makes the sub-bubbles within this bubble different from each other?"

### Correlation as extra context
- In the sidebar, each axis shows its strongest correlation with a hand-crafted axis
- E.g. "↔ Cultural vs. political r=+0.87"
- This helps the researcher interpret the statistical axis without it becoming the name

---

## 6. Visualization (v4)

### General feel
- Dark, minimalist, no UI clutter
- Animations are purposeful — they communicate structure, not decoration
- It should feel like the tool is alive and evolving

### Macro view
- 3–5 large bubbles positioned on the 2 most distinguishing global axes
- Bubble size = number of accounts (sqrt-scaled)
- Clicking a bubble = that bubble "pops"

### Pop animation
- Sub-bubbles start at the exact position of the parent bubble
- They animate smoothly to their local PCA position
- **The parent is the center** — sub-bubbles are offsets relative to the parent, not absolute canvas coordinates
- This keeps the parent always at the center of its children
- Other macro-bubbles stay visible but dimmed in the background

### Navigation
- **Unlimited layers**: you can zoom into any sub-bubble that has sub-sub-clusters
- **State as a stack**: `state.path = []` is macro, `['1']` is in cluster 1, `['1', '1b']` is deeper
- **Breadcrumb**: shows the full navigation path, each step clickable
- **Back button**: goes one layer back
- **Clicking a dimmed background bubble**: navigates to that bubble (first collapse, then pop)

### Filters
- The sidebar shows filter chips for all clusters at the current level
- Clicking a chip: toggle visibility
- Hidden clusters are dimmed but still visible (so you see the structure)

### Sidebar
- **Active axes**: name, eta², hi/lo labels, rationale (if available), correlation with hand-crafted axis
- **Filter chips**: per cluster at the current level
- **Cluster info**: name and description of the active cluster when zoomed in
- Never hashtags as a label in the sidebar

### Tooltip on hover
- Name of the cluster, number of accounts, total reach (followers)
- Hashtags ARE allowed here as contextual info — you deliberately hover over it
- "Click to zoom in →" hint if the cluster has sub-clusters

---

## 7. Pipeline

Fully automatic after `python3 pipeline.py`:

1. **Collection** — scrape accounts and videos
2. **Hashtag tracker** — which tags are growing, which are new
3. **Sound tracker** — original sounds as a bubble signal
4. **Bubble analysis** — Louvain + variance-based recursion → graph.json
5. **Auto-name clusters** — `name_clusters.py` via the Claude API
6. **Visualization** — `generate_viz.py` for v2
7. **Axis scores** — `axis_scorer.py` hand-crafted axes
8. **PCA axes** — `pca_axes.py` data-driven axes + local PCA per cluster
9. **Auto-name axes** — `name_axes.py` via the Claude API *(to be added to the pipeline)*
10. **History snapshot** — time series of cluster growth
11. **Strategy report** — seeds for the next run

The manual step after the pipeline:
- Review strategy.py output
- Decide which bridge accounts and hashtags are promising
- Add to SEED_ACCOUNTS / SEED_HASHTAGS in config.py
- Choose: expansion run or bootstrap

---

## 8. Technical principles

- **No hardcoding in visualizations.** Always `fetch()` to JSON, never cluster data in the JS.
- **One data source.** `bubblescape.db` is the single source of truth. All JSON exports are derived.
- **Fingerprint system for stability.** Names are tied to hashtag fingerprints, not to cluster indices (which change after Louvain).
- **Claude API for naming.** `name_clusters.py --all` re-names everything. `name_axes.py` names the PCA axes. Both run automatically in the pipeline if `ANTHROPIC_API_KEY` is available.
- **Spend credits deliberately.** Pre-check on known followers, strategic seeds, MAX_VIDEO_PAGES as a deliberate choice.
- **Never commit:** `.env`, `data/bubblescape.db`, API keys.

---

## 9. What is still on the wishlist (not yet built)

- **User-selectable axes in v4**: default to the 2 best, but let the user switch to other PCs or hand-crafted axes via a dropdown
- **Time-series view**: how bubbles have grown and split across runs (history_append.py already collects the data)
- **Subtitles/transcriptions**: KonbiniAPI does not support this yet — feature request outstanding
- **More runs**: the current dataset is 315 accounts, 47K videos — still relatively small for stable clusters
- **name_axes.py added to pipeline.py** as a step after pca_axes.py
