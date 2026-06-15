# Bubblescape

A research tool that maps algorithmic filter bubbles on Dutch TikTok: it scrapes accounts, builds a hashtag co-occurrence graph, detects communities with Louvain, and visualizes the structure as an interactive bubble map.

> **A bubble** is a partially closed actual domain — a structured set of empirical exposures produced by the conjoint operation of algorithmic mechanisms, social structures, and individual agency. Bubbles are emergent properties, not purely psychological (confirmation bias) nor purely structural (algorithmic determinism). They persist because the structural mechanisms producing them are largely unobservable to the people inside them.

Technically, bubbles are clusters in a hashtag co-occurrence graph. There is no follow graph — TikTok following lists are private, so the tool measures **content producers, not viewers**.

---

## What this repository contains

- **A Python data pipeline** — collection (KonbiniAPI), graph construction, Louvain community detection, two systems of interpretive axes (hand-crafted + PCA), and automatic cluster/axis naming via the Claude API.
- **Interactive visualizations** (`v2/`, `v3/`, `v4/`) — a bubble-packing map, semantic axis maps, and a zoomable interactive bubble map.
- **Documentation** (`docs/`) — the project state, design principles, research notes, and visualization spec, in English (`docs/en/`) and the original Dutch (`docs/nl/`).
- **Aggregate result data** (`data/`) — cluster names, PCA axes, and axis scores (no personal data; see *Data & privacy* below).

---

## Repository layout

```
bubblescape/
├── pipeline.py              # main entrypoint — runs the full pipeline
├── config.py               # seeds, collection limits, Dutch filter (keys via .env)
├── collector.py  db.py  session.py          # collection + storage
├── analyze.py  pca_axes.py  axis_scorer.py  # graph, clustering, axes
├── name_clusters.py  name_axes.py           # Claude-API naming
├── hashtag_tracker.py  sound_tracker.py  sound_graph.py
├── strategy.py  history_append.py  generate_viz.py  debug.py
├── bubbletree/bubbletree_export.py          # data export for v4
├── data/                   # aggregate, PII-free result JSON
├── docs/en/  docs/nl/      # documentation (English + Dutch)
├── v2/  v3/  v4/           # visualizations
├── requirements.txt
├── .env.example
└── seed_accounts.example.txt
```

---

## Setup

```bash
# 1. Install dependencies
python3 -m pip install -r requirements.txt

# 2. Configure API keys
cp .env.example .env
#   then edit .env and add your KONBINI_API_KEY (required)
#   and optionally ANTHROPIC_API_KEY (enables cluster/axis naming)

# 3. (Optional) provide seed accounts
cp seed_accounts.example.txt seed_accounts.txt
#   then add TikTok handles, one per line. Bootstrap also works from seed
#   hashtags alone if you skip this.
```

API keys are **never hardcoded** — every script reads them from the environment via `python-dotenv`.

---

## Running it

```bash
python3 pipeline.py              # expansion run (extends an existing dataset)
python3 pipeline.py --bootstrap  # fresh start from the seed hashtags/accounts
python3 pipeline.py --publish    # expansion + version bump if a threshold is crossed
```

The pipeline runs 10 steps end-to-end: collect → track hashtags → track sounds → build & cluster the graph → name clusters → build the v2 visualization → score axes → PCA axes → history snapshot → strategy report. After a run you review the strategy output and decide which new seeds to add — that judgment step is intentionally manual.

### Viewing the visualizations

`v2` works by opening the HTML directly. `v4` loads data via `fetch()`, so serve the repo over HTTP:

```bash
python3 -m http.server 7827
# then open http://localhost:7827/v4/
```

---

## Data & privacy

This is a public repository, so account-level personal data is **deliberately excluded**:

| Committed (`data/`) | What it is |
|---|---|
| `cluster_names.json` | Cluster names/descriptions, keyed by hashtag fingerprint |
| `pca_axes.json` | PCA results, axis names, and loadings |
| `axis_scores.json` | Per-cluster scores on the hand-crafted axes |

These three files are aggregate and contain **no usernames, bios, or per-account rows**.

**Not committed** (gitignored): the raw SQLite database (`data/bubblescape.db`), the account-level graph (`data/graph.json`, which contains real handles and bios), derived caches (`sounds.json`, `hashtags.json`), and the real seed-account handles (`seed_accounts.txt`). Seed accounts are described methodologically — the largest Dutch creators taken from public ranking sites (HypeAuditor, Modash, Marketingreport.nl) plus a small set of political accounts — but the specific handles are not published. Running the pipeline locally regenerates the excluded files from the database.

---

## Method

For the full methodology, theoretical framing (Bhaskarian critical realism), and the research direction toward a nested stochastic block model, see [`docs/en/RESEARCH_NOTES.md`](docs/en/RESEARCH_NOTES.md), [`docs/en/PROJECT_STATE.md`](docs/en/PROJECT_STATE.md), and [`docs/en/DESIGN_PRINCIPLES.md`](docs/en/DESIGN_PRINCIPLES.md).
