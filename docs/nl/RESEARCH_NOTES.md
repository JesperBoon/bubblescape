# Bubblescape — Research Notes

*Carry-over document for the research/paper track. Start a fresh chat with this file +
`VISUALIZATION_STATE.md` + `v3/STRATA_DATA_CONTRACT.md`. This captures the theory, the
prototypes, and the agreed direction so no context is lost between sessions.*

Last updated: 2026-06-13

---

## 0. The one-sentence thesis

> Bubblescape is both a *visualization* of algorithmic filter bubbles on Dutch TikTok **and**
> a *method* for detecting community structure that respects Bhaskarian (critical-realist)
> stratification — where what counts as "real" structure is what stays stable across scale,
> not what a single observer-chosen threshold happens to produce.

The paper-worthy contribution is the bridge: **critical realism ↔ Bayesian network science.**
Not "another community-detection viz."

---

## 1. Theoretical frame (Bhaskar)

Three-layer ontology, used as the fixed interpretive lens:

| Layer | Meaning | In the data | In the view |
|---|---|---|---|
| **REAL** | everything that exists, incl. unobserved mechanisms | the universe of accounts | outer faint/dashed field |
| **ACTUAL** | patterns the model infers at a given scale | inferred blocks | semi-transparent regions |
| **EMPIRICAL** | what we actually observe | accounts + their posts | the dots |

Working definition of a bubble (use for cluster labels / academic framing):
> A bubble is a partially closed actual domain — a structured set of empirical exposures
> produced by the conjoint operation of algorithmic mechanisms, social structures, and
> individual agency. Bubbles are emergent properties, not purely psychological
> (confirmation bias) nor purely structural (algorithmic determinism). They persist because
> the structural mechanisms producing them are largely unobservable to the people inside them.

**Honesty constraint:** the tool measures content **producers, not viewers** — who posts what,
not who sees what. No follow graph (following lists are private on TikTok). Emit
`null` / "I don't know" rather than inventing structure.

### Two failure modes the method must avoid
- **Epistemic fallacy** — collapsing what *is* into what we can *measure*. Louvain finds
  "communities" even in random graphs; modularity confuses a measurement artifact for structure.
- **Actualism / flat ontology** — pretending reality has one uniform grain. A single global
  resolution parameter imposes uniform stratification across the whole graph.

### The realness criterion (the core methodological move)
**Structure that is robust across a sweep of the scale parameter is treated as real;**
structure that appears at only one parameter setting is an *artefact of the observer*.
Operationalized as plateaus in the sweep (MDL drops / effective-information peaks /
partition stability). "Turn the dial — the layers that hold still are the real ones."

---

## 2. Method direction

### Away from Louvain (keep it only as the foil)
- Louvain/modularity: empirical fallacy in code; resolution limit (Fortunato & Barthélemy 2007);
  finds communities in random graphs (Peixoto). Use it in the paper as the *contrast*, not the tool.

### Toward a nested, degree-corrected, (over)lapping SBM
- **Peixoto / graph-tool** nested SBM: parameter-free, MDL/Bayesian, infers a discrete hierarchy,
  avoids the modularity resolution limit, yields a posterior over partitions.
- Each level's prior is set by the structure one level up → **every branch gets locally-adapted
  granularity for free.** This is what makes "zoom changes the scalar" principled (see §4, Q1).
- Risk flagged: graph-tool install is the main practical hurdle.

### Multi-resolution / flow alternatives (candidates for the "dial")
- Reichardt–Bornholdt resolution γ (2006; in leidenalg / python-louvain).
- Markov Stability (Delvenne/Yaliraki/Barahona 2010) — continuous Markov-time t knob.
- Infomap / map equation (Rosvall) — flow-based; a bubble = trapped attention.
- Causal emergence / effective information (Hoel 2013; Klein & Hoel 2020, `einet`) — picks the
  scale where the macro description carries the most causal information.

### Key technical facts to keep honest
- **Detectability ceiling:** number of detectable blocks scales ~**√N**.
  - 54 mock accounts → ~7 blocks (why the mock "bursts" all at once — small-data artifact).
  - ~1,000 accounts → ~31 blocks.
  - ~2,600 Dutch accounts (real) → ~50 finest blocks.
- Resolvable **depth grows ~log N**, not the parameter *range*. More data = more distinguishable
  plateaus inside a fixed range, not a wider dial.

---

## 3. The graph itself (what edges mean)

- No follow graph. Build from **bipartite** account×feature data:
  account×hashtag and account×sound (and potentially account×co-engagement).
- Current pipeline (VISUALIZATION_STATE.md): hashtag co-occurrence → stopword filtering
  (remove hashtags in >40% of accounts: fyp, foryou, viral, etc.; remove <3-account tags;
  remove contamination like liverpool/premier league) → community detection.
- The bubble emerges as a **coupling** of an audience-block and a content-regime-block
  (bipartite). "The bubble *is* the coupling."

---

## 4. The two open research questions (the next paper sections)

### Q1 — Scale is locally variable (the vertical / Bhaskarian axis)
A global dial assumes uniform grain. It isn't: politics may hold 5 real sub-strata, a niche none.
**Zooming into a bubble should re-run the scale sweep restricted to that subgraph**, with its own
local plateaus. The nested SBM delivers this natively (per-branch local resolution). Claim:
*strata are not uniform in depth; the real is differentiated; depth of stratification varies by region.*

### Q2 — Similarity is not one thing (the edge / modes-of-the-actual axis)
Position in the current prototypes is **meaningless** (deterministic radial layout). In a real
embedding, axes are emergent and uninterpretable. But accounts are similar in *different ways*
(political, cultural, nationalist, taste/interest…). Decompose similarity into interpretable axes:
- **Feature factoring** — NMF / topic factors over hashtags+sounds → interpretable "ways of being
  similar." Choose factors → x/y axes → reconnects to `axis_view`.
- **Multilayer SBM** — model each kind-of-similarity as a network layer, fit jointly.

### The synthesis (build the paper around this)
- Q1 = **scale** axis (vertical, ontological, locally variable depth).
- Q2 = **edge** axis (the modes of the actual, what kind of similarity binds a bubble).
- A **multilayer nested SBM** is the single object delivering both. Position becomes a chosen 2D
  slice of membership space with *interpretable* axes — reuniting all three prototypes.
- → **One paper theoretically; two–three visual prototypes practically.** Do not spin Q2 off as a
  separate project; it's the same model viewed from the edge side.

---

## 5. The prototypes (current state)

All under `v3/`. Self-contained HTML, no build step, EN/NL toggle, TikTok-dark aesthetic.
**Constraint: do NOT modify `v2/`, `bubbletree/bubbletree_export.py`, or `bubbletree_data.json`.**

| File | What it shows | Status |
|---|---|---|
| `v3/strata_view.html` | Stratified overlapping view: REAL/ACTUAL/EMPIRICAL, soft membership, bridge accounts, zoomable hierarchy, bipartite coupling view, account panel | Built, browser-verified |
| `v3/axis_view.html` | Semantic axis map (earlier prototype) | Exists (pre-dates this track) |
| `v3/emergence_view.html` | **Continuous scale-dial**: bubbles split/merge live as γ/t sweeps; robust-across-sweep clusters flagged green ("REAL"), ephemeral ones dashed; stability strip with plateaus; per-account scale-trace | Built, browser-verified |
| `v3/STRATA_DATA_CONTRACT.md` | The JSON shape the model team must emit so the renderer goes live | Written |

`emergence_view.html` mechanics (so it can be extended): a mock `TREE` with `splitAt` thresholds;
gaps between thresholds = plateaus; wide gap = robust. `clustersAt(g)` cuts the tree at dial value g.
17 nodes pre-created and toggled (no ghost elements). `loadData()`-style swap point: replace the
hardcoded `TREE` with a real exported sweep; renderer unchanged. Robust plateaus at γ≈0.25/0.55/0.85
(3/6/9 bubbles). **Position is currently non-semantic** (radial layout) — this is the Q2 gap.

---

## 6. Data reality

- DB: `/Users/jesperboon/Documents/bubblescape/data/bubblescape.db` (local only, never commit).
- ~4,900 accounts total / ~2,600 fully-collected Dutch / ~127k videos.
- Prototypes currently use ~54–90 **mock** accounts. The gap to real data is the main reason the
  prototypes feel less specific and "burst all at once" (see §2 √N facts).

---

## 7. Next steps (ordered)

1. **Paper-thinking session** — pressure-test the theory before more code (see prompt handed off).
   Key things to resolve: is robustness-across-scale defensible as a realness criterion? does the
   nested-SBM local-resolution claim hold up? how to validate against ground truth we don't have?
2. **Build: local-reparameterization zoom** — drilling into a bubble re-runs the sweep on the
   subgraph with its own plateaus (Q1). Still mock-able.
3. **Build: multi-axis similarity / interpretable position** — factor similarity into named axes;
   let the user pick x/y; color bubbles by their cohesion mode (Q2).
4. **Real pipeline** — `build_sweep.py`: DB → bipartite account×hashtag(+sound) graph → nested
   (eventually multilayer) SBM via graph-tool → export the sweep as JSON in the contract shape →
   swap the prototype's mock for `fetch()`. graph-tool install is the risk.

---

## 8. Open questions to keep nagging at

- What *validates* a "real" stratum when there's no ground truth? (robustness is necessary, is it
  sufficient?) Temporal persistence across DB snapshots is a candidate second criterion.
- Mixed-membership vs hard blocks: how to render an account that is genuinely 60/40 without faking
  precision.
- Does causal-emergence / effective-information give a *better* realness signal than raw partition
  stability, or just a more expensive one?
- How to present uncertainty honestly in a tool meant for journalists/policymakers, not just
  network scientists.
