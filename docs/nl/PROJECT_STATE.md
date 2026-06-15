# Bubblescape — Project State Document
*Update this file at the end of every session. Use it to start every new conversation.*
*Also read DESIGN_PRINCIPLES.md for the design philosophy and full wishlist.*
*For the research/paper track (critical-realist method, nested SBM direction, v3 research prototypes), see RESEARCH_NOTES.md.*

---

## Wat is Bubblescape?

Een onderzoekstool die algoritmische filterbubbels op Nederlandse TikTok in kaart brengt. Accounts worden gescraped, een hashtag-co-occurrence graaf wordt gebouwd, Louvain detecteert communities, en de structuur wordt gevisualiseerd als een interactieve bubbelkaart.

**Theoretische definitie (keep this precise):**
> A bubble is a partially closed actual domain — a structured set of empirical exposures produced by the conjoint operation of algorithmic mechanisms, social structures, and individual agency. Bubbles are emergent properties, not purely psychological (confirmation bias) nor purely structural (algorithmic determinism). They persist because the structural mechanisms producing them are largely unobservable to the people inside them.

**Technisch:** bubbels = clusters in een hashtag-co-occurrence graaf. Geen follow-graaf (following lists zijn private).

---

## API: KonbiniAPI

- Base URL: `https://api.konbiniapi.com/v1`
- Auth: Bearer token in Authorization header
- 1 credit = 1 API call
- **Credits remaining: ~18,000** (schatting, nog geen nieuwe scrape-sessie gedraaid op 2026-06-10)
- Credits vervallen ca. **2026-06-25** (15 dagen vanaf 2026-06-10)

**Kritieke API-bevindingen:**
1. Responses zijn gewrapped in `{"data": {...}}` — altijd unwrappen
2. Video-hashtags zitten in `"tag"` (niet `"hashtags"`), als objecten `{"name": "pvv", "entityId": "..."}`
3. Video `"content"` = plain string (caption)
4. Video ID = `"entityId"` (numeriek), niet `"id"` (URL)
5. Following lists zijn PRIVATE — 0 resultaten. Niet gebruiken voor graaf
6. Tag endpoint `/tiktok/tags/{tag}/videos` → 404 (broken)
7. Search endpoint `/tiktok/search/videos?query={tag}` → werkt
8. Paginering via `"nextCursor"`
9. 502/500 errors bij snelle requests → random 0.5–2s sleep in `_get()`
10. Audio metadata altijd beschikbaar: `audio_id` + `audio_title`
11. **Geen subtitles/transcripties beschikbaar** — feature request ingediend

---

## Feedback voor KonbiniAPI developer

*Gebaseerd op onderzoekservaring met 1,699 accounts en 47,192 videos op Nederlands TikTok. Relevant voor productontwikkeling van de API.*

### Kritieke ontbrekende datavelden

**1. Videotaal / taaldetectie**
Het grootste classificatieprobleem in ons onderzoek: we kunnen niet betrouwbaar zeggen of een account in het Nederlandse informatie-ecosysteem zit zonder de taal van de video's te kennen. We moeten nu `langdetect` draaien op captions als proxy. Een `language` veld per video (of per account op profielniveau) zou dit triviaal maken en credits besparen — nu collecten we een account volledig voordat we weten of hij relevant is.

**2. Subtitels / transcripties**
Al ingediend als feature request. Cruciaal voor inhoudelijke analyse: een politicus die spreekt zonder hashtags is nu volledig onzichtbaar in onze graaf. Veel institutionele accounts (NOS, RTL, partijaccounts) gebruiken geen hashtags maar wel rijke gesproken content. Zonder transcripties missen we het inhoudelijke signaal van juist de accounts met het grootste bereik.

**3. Bio altijd teruggeven in profiel-response**
Uit onze data: ~35% van Dutch accounts heeft een informatieve bio. Bio-tekst is het rijkste beschikbare signaal voor accountclassificatie — rijker dan hashtags, en beschikbaar voor accounts die geen hashtags gebruiken. Verificatie nodig: geeft `/tiktok/users/{username}` altijd `bio` terug, ook als die leeg is? Of wordt het veld soms weggelaten?

### Data-kwaliteitsobservatie: hashtag-armoede bij institutionele accounts

Een belangrijk patroon: grote, betrouwbare Nederlandse accounts (omroepen, politieke partijen, nieuwsmedia) gebruiken systematisch **geen of nauwelijks hashtags** in hun video's. Voorbeelden uit ons corpus:

| Accounttype | Volgers | % videos met hashtags |
|---|---|---|
| Politieke partij | 115K | 5% |
| Entertainment/humor-platform | 832K | ~10% |
| Commerciële omroep | 643K | ~15% |
| Publieke sportredactie | 407K | ~20% |

Deze accounts vertrouwen op algoritmische reach, niet op hashtag-discovery. Voor een onderzoeker die filterbubbels in kaart brengt zijn dit juist de meest waardevolle datapunten — maar ze zijn onzichtbaar in een hashtag-gebaseerde graaf.

**Implicatie voor API-design:** een `topics` of `content_categories` veld per video (door TikTok zelf gegenereerd voor targeting) zou dit gat dichten. TikTok kent deze categorieën intern — ze zijn de basis van het aanbevelingssysteem.

### Broken endpoint

- `/tiktok/tags/{tag}/videos` → geeft consistent 404. We gebruiken `/tiktok/search/videos?query={tag}` als workaround. Zou fijn zijn als het tag-endpoint gerepareerd wordt — de search endpoint geeft soms minder gerichte resultaten.

### Het discovery-probleem: hashtag-gebaseerde zoekresultaten zijn niet nationaal gefilterd

Het fundamentele probleem bij het opbouwen van een nationaal TikTok-corpus: **de search API geeft geen taalfilter of landfilter**. Als je zoekt op `#grappig` of `#schoonmaken`, krijg je een mix van Nederlandse én internationale accounts terug die toevallig die hashtag gebruiken. Dit forceert ons tot een duur post-hoc Dutch filter (profiel ophalen + video's ophalen + taaldetectie), terwijl een `?language=nl` of `?country=NL` parameter dit triviaal zou maken.

**Kwantitatief effect:** zonder taalfilter is ~50% van de gevonden accounts niet-Nederlands. Dat kost ~2 credits per non-Dutch account om te rejecten (profiel + eerste videopagina). Met een landfilter zou de efficiency van ~7 credits/Dutch account naar ~3-4 kunnen dalen — een verdubbeling van het scraping rendement.

### Het mega-account probleem: internationale creators met Nederlands publiek

Een verwant probleem: grote internationale accounts (skysports, CBS, ESPN) verschijnen soms in zoekresultaten op Dutch hashtags omdat ze een grote Nederlandse kijkerspopulatie hebben. TikTok's eigen targeting weet dit onderscheid — het verschil tussen *creator nationality* en *audience nationality*. Een `creator_country` veld per account (los van het publiek) zou dit direct oplossen.

### 502 errors — ook op de search endpoint, niet alleen op video-endpoints

Bij te snelle paginering geeft de API inconsistent 502-errors terug. We hebben een `random.sleep(0.5, 2.0)` ingebouwd als workaround. Maar op 2026-06-12 zagen we 502-errors ook op `/tiktok/search/videos` — de search endpoint zelf, bij normaal gebruik en ruime sleep. Dit is anders dan de rate-limiting 502s op `/users/{username}/videos`. Het lijkt op periodieke instabiliteit aan de serverkant, los van onze request-snelheid.

Effect: als de search endpoint faalt midden in een chain discovery probe, kan de sessie vastlopen (geen response, geen timeout-melding, gewoon stilte). We hebben een `timeout=20s` op requests, maar bij een 502 die een partial response geeft kan dit meer dan een minuut duren voordat Python de connectie verbreekt.

**Aanbeveling voor de developer:** een `Retry-After` header bij 502-responses zou clients in staat stellen intelligent te wachten in plaats van vast te lopen. En/of een `X-RateLimit-Remaining` header zodat clients de snelheid kunnen aanpassen voordat errors optreden.

### Positieve bevindingen (wat goed werkt)

- Audio metadata (`audio_id`, `audio_title`) altijd beschikbaar en betrouwbaar — waardevol als alternatief co-occurrence signaal naast hashtags
- Caption/description (`content`) veld: 95% gevuld, gemiddeld 165 tekens — bruikbaar voor NLP-analyse
- Paginering via `nextCursor` werkt stabiel
- Volgers-count op profielniveau betrouwbaar genoeg voor filtering

---

## Codebase

**Locatie:** `~/Documents/bubblescape/`
**GitHub:** `github.com/JesperBoon/bubblescape`

### Python scripts

| File | Doel |
|---|---|
| `pipeline.py` | **Hoofdentrypoint** — 10 stappen volledig automatisch |
| `config.py` | API keys, seed hashtags/accounts, collectielimits, Dutch filter |
| `collector.py` | KonbiniAPI client — alle API calls, paginering, sleep, credit tracking |
| `db.py` | SQLite schema + read/write helpers |
| `session.py` | Session runner — bootstrap, collect, Dutch filter, credit pre-check |
| `analyze.py` | Louvain + variance-based recursie → graph.json (emergente diepte) |
| `hashtag_tracker.py` | Post-sessie hashtag-analyse — nieuwe discovery-tags |
| `sound_tracker.py` | Post-sessie audio-analyse — geluiden die verspreiden |
| `axis_scorer.py` | 6 hand-crafted semantische assen per cluster (0–1 genormaliseerd) |
| `pca_axes.py` | TF-IDF + PCA → data-driven assen + lokale PCA per cluster + eta² |
| `name_axes.py` | **NIEUW** — benoemt PCA-assen via Claude API (naam, rationale, hi/lo labels) |
| `name_clusters.py` | Benoemt clusters via Claude API — geen hashtags als naam |
| `history_append.py` | Append-only snapshot na elke pipeline run |
| `strategy.py` | **NIEUW** — bridge accounts, outliers, axis gaps, hashtag gaps voor volgende run |
| `sound_graph.py` | Louvain op sound co-occurrence (parallel aan hashtag-graaf) |
| `generate_viz.py` | Bouwt v2 bubblescape.html |
| `bubbletree/bubbletree_export.py` | Exporteert bubbletree_data.json voor v4 (incl. account_memberships) |
| `debug.py` | Raw API response printer |

### Data files (lokaal only, nooit committen)

| File | Inhoud |
|---|---|
| `data/bubblescape.db` | SQLite database — accounts, videos, sessions |
| `data/graph.json` | Louvain-graaf met clusters, sub-clusters, fingerprints |
| `data/cluster_names.json` | Claude-gegenereerde namen gekoppeld aan hashtag-fingerprints |
| `data/pca_axes.json` | PCA resultaten + eta² + correlaties + lokale PCA per cluster + **pc_names** |
| `data/axis_scores.json` | Hand-crafted as-scores per cluster |
| `data/axis_history.json` | Append-only temporele log van as-snapshots |
| `data/strategy.json` | Aanbevelingen voor volgende scrape-sessie |
| `data/hashtags.json` | Hashtag tracker output |
| `data/sounds.json` | Sound tracker output |
| `bubbletree/bubbletree_data.json` | Exportbestand voor v4 visualisatie |

### Collectieparameters (config.py)

- `MIN_FOLLOWERS = 500` — accounts kleiner worden overgeslagen
- `MAX_BOOTSTRAP_FOLLOWERS = 5_000_000` — globale mega-accounts uitgesloten bij bootstrap
- `MAX_VIDEO_PAGES = 2` — ~60 video's per account (was 1; meer = rijkere TF-IDF)
- `MAX_TAG_PAGES = 3` — pagina's per seed hashtag bij bootstrap
- `MAX_CREDITS_PER_SESSION = 500`
- ~2 credits per account (profiel + video's)
- **Credit pre-check:** accounts met bekend <500 volgers worden vóór de API-call overgeslagen

---

## DB-stand (2026-06-12, laatste update)

| Stat | Waarde |
|---|---|
| Accounts ontdekt | ~3,800 |
| Dutch fully collected | 1,106 |
| Non-Dutch excluded | ~1,200 |
| Accounts in frontier | ~1,222 |
| Videos opgeslagen | ~100,000 |
| Credits resterend | ~16,500 |

---

## Huidige clusterstructuur

*Gegenereerd door Louvain op 315 fully collected accounts. Namen via Claude API (name_clusters.py).*

### Cluster 1 — Gewoon Nederlands TikTok
**126 accounts · kleur: #1D9E75**
Sub-clusters:
- Dutch TikTok Mainstream Creators
- Dutch Football and Culture Commentary
- Dutch Muslim Content Creators
- Dutch Humor and Comedy Creators

### Cluster 2 — Politiek betrokken Nederland
**110 accounts · kleur: #5B4FCF**
Sub-clusters:
- Dutch Political Activism and Civic Engagement
- DENK Party Political Community
- Dutch Right-Wing Political Commentary
- Dutch Left-Wing Political Activists

### Cluster 3 — Gevestigde media en officiële kanalen
**63 accounts · kleur: #D97B2C**
Sub-clusters:
- Dutch Islamic Community Activists
- Dutch Political Commentary and Debate
- Dutch Sports and National Pride
- Amsterdam Tourism and Dutch Culture

### Cluster 4 — Dutch News and Current Affairs
**11 accounts · kleur: #C94040**
Sub-clusters:
- Dutch News and Local Updates
- Flemish Education and Politics Activists

*Opmerking: cluster 4 is klein (11 accounts) en waarschijnlijk een artefact van Vlaamse media. Groeit mogelijk bij volgende run.*

---

## PCA-assen (volledig benoemd, 2026-06-09)

*Namen gegenereerd door `name_axes.py` via Claude API. Opgeslagen in `data/pca_axes.json` onder `pc_names`.*

| PC | Naam | eta² | Hoog uiteinde | Laag uiteinde |
|---|---|---|---|---|
| PC5 | Politieke focus vs. algemeen bereik | 0.505 | Politieke nieuwsaccounts | Algemeen viraal bereik |
| PC6 | Entertainment versus Partijpolitiek | 0.495 | Virale entertainmentcontent | Institutionele partijpolitiek |
| PC3 | Politieke niche vs. algemeen bereik | 0.396 | Politieke nichecontent | Algemeen viraal bereik |
| PC7 | Institutionele politieke focus | 0.395 | Formeel politiek nieuws | Virale politieke content |
| PC2 | Politieke niche vs. algemeen bereik | 0.236 | Rechtse partijpolitiek | Virale algemeenheden |
| PC8 | Cultureel-mainstream vs. partijpolitiek | 0.217 | Culturele mainstream content | Expliciete partijpolitiek |
| PC1 | Institutioneel vs. Populistisch Rechts | 0.152 | Institutioneel-links | Populistisch-rechts viraal |
| PC4 | Populistisch-rechts versus partijpolitiek | 0.072 | Nationalistisch-populistisch | Gevestigde partijpolitiek |

---

## Hand-crafted assen (axis_scorer.py)

| As | Meet | Laag | Hoog |
|---|---|---|---|
| `political_lr` | Links-rechts spectrum | Links (d66, groenlinks) | Rechts (pvv, fvd, wilders) |
| `institutional_trust` | Vertrouwen in instituties | Wantrouwen (fvd, complot) | Vertrouwen (nos, nrc) |
| `religious_identity` | Islamitische identiteitssignalen | Seculier | Religieus |
| `bubble_closure` | Interne dichtheid van cluster | Open | Gesloten |
| `cultural_vs_political` | Leefstijl vs. politiek | Politiek | Cultureel |
| `reach` | Log(totaal volgers in cluster) | Klein bereik | Groot bereik |

---

## Visualisaties

### v2 — Bubble-packing (canonieke viz)
- **Pad:** `v2/bubblescape.html`
- **URL:** `http://localhost:7825/v2/bubblescape.html`
- Bubble-packing D3, 3 zoomniveaus, EN/NL toggle, edge overlay, Bubble Finder AI
- Data zit inline (hardcoded) — niet ideaal, maar werkt

### v3 — Semantische as-kaart
- **Pad:** `v3/axis_view.html`
- **URL:** `http://localhost:7826/v3/axis_view.html`
- Scatter plot met instelbare X/Y assen, 6 hand-crafted + 8 PCA-assen

### v3 — Research-track prototypes (kritisch-realisme / SBM-richting)
*Mock-data prototypes voor de paper-track. Volledige context in `RESEARCH_NOTES.md`.*
- **`v3/strata_view.html`** — gelaagde overlap-weergave: REËEL/ACTUEEL/EMPIRISCH, soft membership, bridge-accounts, zoombare hiërarchie, bipartiete coupling-view, account-paneel. Bouwt tegen `v3/STRATA_DATA_CONTRACT.md`.
- **`v3/emergence_view.html`** — continue schaal-knop (γ / Markov-tijd t): bubbels splitsen/smelten live; clusters die robuust zijn over de sweep worden groen ("REËEL") geflagd, vluchtige gestreept; stabiliteitsstrip met plateaus; per-account schaal-spoor.
- **`v3/STRATA_DATA_CONTRACT.md`** — de JSON-vorm die het model-team moet exporteren om de renderer live te zetten.
- *Let op:* positie is in deze prototypes nog niet-semantisch (radiale layout) — dat is de Q2-gap (zie RESEARCH_NOTES §4).

### v4 — Interactieve bubbelkaart (actief in ontwikkeling)
- **Pad:** `v4/index.html`
- **URL:** `http://localhost:7827/v4/`
- **Laadt dynamisch:** `fetch('/bubbletree/bubbletree_data.json')` + `fetch('/data/pca_axes.json')`
- Pop-animatie: sub-bubbels spreiden uit vanuit parent als middelpunt
- Unlimited lagen via `state.path[]` navigatiestack
- Assen veranderen per laag (lokale PCA)
- Alle namen via Claude API — nooit hashtags als naam
- Andere macro-clusters blijven zichtbaar maar gedimmed bij inzoomen
- Filter-chips per cluster in sidebar
- Breadcrumb + back-knop voor navigatie

**Server starten:**
```bash
# v4 (en bubbletree data)
python3 -m http.server 7827 --directory /Users/jesperboon/Documents/bubblescape
```

---

## Pipeline starten

```bash
cd /Users/jesperboon/Documents/bubblescape

python3 pipeline.py              # expansion run
python3 pipeline.py --bootstrap  # fresh start vanuit seed hashtags
python3 pipeline.py --publish    # expansion + versie-bump als threshold overschreden

# Losse stappen:
python3 name_clusters.py --all   # herbenoem alle clusters
python3 name_axes.py             # benoem PCA-assen
python3 bubbletree/bubbletree_export.py  # update v4 data
```

---

## Bevestigde bevindingen

- **#vvd collision:** sommige accounts met #vvd bleken Liverpool FC-fans (VVD = Virgil van Dijk, de voetballer), niet politiek
- **Sound graph ≠ hashtag graph:** slechts 1 gedeeld account. Geluiden = algoritmische blootstellingssporen, geen identiteitsmarkers
- **Islam splits drie wegen:** (1) onderwerp van kritiek in rechts cluster, (2) eigen identiteit in progressief/Palestina cluster, (3) devotionele content los van politiek
- **Vlaamse media is algoritmisch Nederlands:** HLN.be, VTM clusteren met Nederlandse broadcast
- **Diffuse variantiestructuur:** PC1 verklaart slechts 5.6% — geen dominante enkelvoudige as
- **PC5/PC6 zijn de sterkste cluster-scheiders** (eta² ~0.50) na naming: politieke focus vs. entertainment

---

## Seed hashtags (config.py)

```
tiktoknl, stemrechts, stemlinks, tweedekamer, moslimsvoornederland,
nederlandspolitiek, nederlandsenieuws, politiektiktok, pvv, wilders,
d66, vvd, groenlinks, bbb, nsc, nederlandopstand, nederlandsetiktok,
fvd, migratie, islam, islamicreminder, ice, islamic, missmontreal,
lauw, wkvoetbal, werk, langlevedeliefde, firstdates, genzteacher,
onderwijs, grappig, dumpert
```

**Seed accounts:** de grootste Nederlandse creators, ontleend aan publieke ranglijsten (HypeAuditor, Modash, Marketingreport.nl), plus enkele politieke accounts. De handles staan niet in de repo (privacy) — zie `seed_accounts.example.txt` voor het formaat.

---

## Pending tasks

1. **Volgende scrape-sessie plannen** — ~189 accounts in frontier, plus nieuwe seeds uit strategy.py. Aanbevolen cadans: 2 weken na 2026-06-09 → ca. 2026-06-23
2. **strategy.py output reviewen** — bridge accounts en hashtag-gaps bekijken voor nieuwe seeds
3. **name_axes.py toevoegen aan pipeline.py** — nu los script, moet stap 8.5 worden (na pca_axes, voor history)
4. **GitHub updaten** — repo heeft nog de oude Instagram-versie
5. **v4 user-selecteerbare assen** — standaard beste 2, maar dropdown om te wisselen naar andere PCs of hand-crafted assen
6. **Tijdreeks-view** — history_append.py verzamelt al data, nog niet gevisualiseerd

---

## Wat een bubbel geldig maakt

1. **Size ≥ 8 accounts**
2. **Internal density ≥ 3%**
3. **Naambaar** — één zin over wie deze mensen zijn
4. **Distinctieve hashtags** — ≥ 2–3 tags zijn over-represented vs. rest van de graaf
5. **Beschrijfbare grens** — je kunt zeggen wat deze bubbel NIET ziet

---

## Session log

| Datum | Credits | Resultaat |
|---|---|---|
| 2026-05-30 | ~150 | Health check, bootstrap pogingen, parser debugging |
| 2026-05-30 | 41 | Clean bootstrap — 590 accounts, 903 videos |
| 2026-05-30 | ~60 | Expansion aborted — 502 cascade |
| 2026-05-30 | 41 | Re-bootstrap met fixes: Dutch filter, 5M cap, random sleep |
| 2026-05-30 | 425 | Expansion — 195 accounts, 7,615 videos. Eerste 3 clusters bevestigd |
| 2026-05-31 | ~500 | Expansion — 272 collected, 13,756 videos. 3 macro-bubbels, 13 sub-clusters |
| 2026-05-31 | 433 | Bootstrap — 961 ontdekt, 580 collected, 20,875 videos |
| 2026-05-31 | 378 | Expansion — FVD cluster valid (22 accounts, 70% density) |
| 2026-05-31 | 62 | Culturele bootstrap — 10 nieuwe seeds, 1,699 accounts ontdekt |
| 2026-06-06 | 0 | Viz overhaul: v2 Bubbletree + Bubble Finder. v3 semantische as-kaart. Eerste history snapshot |
| 2026-06-09 | ~1,600 | Expansion — 1,510 collected, 47,192 videos. PCA herberekend |
| 2026-06-09 | 0 | Credit-efficiëntie fix, analyze.py emergente diepte, pca_axes.py lokale PCA, strategy.py, v4 gebouwd |
| 2026-06-09 | ~150 | name_axes.py + name_clusters.py --all gedraaid. Alle assen en clusters benoemd. v4 fully functional |
| 2026-06-10 | 0 | Grote dataopschoning: is_dutch flag, rebuild_hashtag_profiles, langdetect-rescreening non-Dutch. Graaf: 315→574 nodes, 18M→70M bereik, 16→103 sub-clusters |
| 2026-06-10 | ~2,100 | 7 expansion sessies — frontier-kwaliteitsproblemen geanalyseerd. RTL cluster (Kroatisch/Duits/Luxemburgs) gefixed. Chain discovery gebouwd en getuned. Credits/account gedaald van 22 naar 6.6 |
| 2026-06-12 | ~500 | Bootstrap met lifestyle seeds (grote mainstream creators + tags als boerzoektvrouw, schoonmaken etc.) — totaal andere bubbels gevonden: schoonmaken, mama-content, koken, sport. 1,106 Dutch accounts collected |
| 2026-06-13 | 0 | Research-track: v3/strata_view.html + STRATA_DATA_CONTRACT.md, v3/emergence_view.html (continue schaal-knop, robuust-over-sweep = "reëel"). RESEARCH_NOTES.md gestart — Bhaskar ↔ nested SBM-richting, Q1 lokale herparametrisatie bij inzoomen, Q2 multi-as gelijkenis. Twee vervolg-prompts opgesteld (paper-denksessie + zoom_view.html build) |

---

## Nieuwe sessie starten

Stuur dit naar Claude aan het begin van een gesprek:

> "Ik bouw Bubblescape — een TikTok bubbel visualizer. Lees PROJECT_STATE.md en DESIGN_PRINCIPLES.md voor volledige context. Vandaag wil ik: [doel]."
