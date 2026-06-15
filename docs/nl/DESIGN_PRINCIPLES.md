# Bubblescape — Design Principles & Wenslijst

Samengesteld uit alle gesprekken. Dit is wat Jesper wil.
Gebruik dit document aan het begin van elke nieuwe sessie.

---

## 1. Wat is Bubblescape eigenlijk?

Bubblescape is een onderzoekstool die algoritmische filterbubbels op Nederlandse TikTok in kaart brengt. Het scrapet accounts, bouwt een hashtag-gebaseerde graaf, detecteert communities via Louvain, en visualiseert die als een interactieve bubbelkaart.

Het is geen statisch dashboard. Het is een levend systeem dat met elke scrape-sessie nieuwe structuur kan onthullen. Bubbels splitsen, verschuiven, verdwijnen — dat is het punt.

Het onderzoeksdoel: begrijpen hoe informatiebubbels op TikTok eruitzien, wat ze van elkaar onderscheidt, en of je ze kunt benoemen als coherente communities.

---

## 2. Data & Scraping

### Wat we scrapen
- TikTok-accounts: username, display name, bio, volgersaantal, hashtags uit recente video's
- Video's: hashtags, geluiden, taal, datum — voor TF-IDF hashtag-profielen
- We scrapen GEEN ondertitels of transcripties (KonbiniAPI biedt dit niet)

### Hoeveel hashtags per account
- **MAX_VIDEO_PAGES = 2** — circa 60 video's per account
- Dit was 1 pagina (30 video's). Meer pagina's = rijkere TF-IDF profielen = betere clustering
- Meer pagina's kost meer credits maar is het waard voor kwaliteit

### Efficiëntie
- **Accounts met <500 volgers worden voor de API-call gefilterd** als dat al bekend is in de DB
- Dit scheelt ~274 credits per run (was een verspilling: eerst 1 credit profiel ophalen, dan weggooien)
- `get_frontier_usernames()` geeft `(username, known_followers)` terug zodat we pre-checken

### Strategisch scrapen
- Niet blind de frontier volgen — dat geeft willekeurige uitbreiding
- Na elke run genereert `strategy.py` een rapport met:
  - **Bridge accounts**: zitten tussen clusters, leiden naar onontdekte communities
  - **Outlier accounts**: lage fit-score in hun cluster, mogelijk begin van een nieuwe bubbel
  - **Axis coverage gaps**: dimensies die weinig spreiding hebben, hashtag-seeds om te verbeteren
  - **Unseen hashtags**: tags die voorkomen bij bridges/outliers maar nog niet in SEED_HASHTAGS
- De researcher beslist zelf welke seeds veelbelovend zijn — dat oordeel is bewust handmatig

### Bootstrap vs. expansion
- `--bootstrap`: vers beginnen vanuit SEED_HASHTAGS en SEED_ACCOUNTS in config.py
- geen flag: frontier uitbreiden vanuit bestaande data
- Na een bootstrap veranderen Louvain-clusters vaak van samenstelling

---

## 3. Clustering & Bubbels

### Emergente structuur
- **Geen vaste dieptegrens.** Louvain mag zo diep gaan als de data het rechtvaardigt
- Stopcriteria zijn data-gedreven:
  - `MIN_VARIANCE_RATIO = 0.10`: een split moet ≥10% van de hashtag-entropie verklaren
  - `MIN_SPLIT_CONFIDENCE = 0.05`: sub-clusters moeten voldoende intern samenhang hebben
  - `LIFT_DEPTH_INCREMENT = 0.8`: lift-drempel stijgt per laag, zodat splits vanzelf stoppen
  - `MAX_DETECTION_DEPTH = 8`: alleen een veiligheidsgrens, geen functionele grens
- Een cluster met weinig intern onderscheid splitst gewoon niet

### Stabiliteit over runs
- Louvain is niet-deterministisch — na een nieuwe run kunnen clusters van samenstelling wisselen
- **Fingerprint-matching**: clusters worden geïdentificeerd op basis van hun top-5 hashtags (gesorteerd, komma-gescheiden)
- Namen en beschrijvingen worden opgeslagen in `cluster_names.json` gekoppeld aan die fingerprint
- Als een fingerprint verandert (cluster is significant herschikt) krijgt het cluster een nieuwe naam via Claude

### Nooit vaste clusters
- Bubbels zijn hypotheses, geen feiten. Ze moeten altijd kunnen veranderen
- Nieuwe data kan leiden tot: splitsen van een bestaande bubbel, samenvoegen, verschuiven
- De visualisatie moet dit reflecteren — geen "dit zijn DE drie bubbels"

---

## 4. Namen — Clusters én Assen

Dit is een hard principe: **nergens in de tool staan hashtags als naam of label.**

### Cluster-namen
- Alle macro-clusters en sub-clusters krijgen een beschrijvende naam via `name_clusters.py` (Claude API)
- De naam beschrijft de *community-identiteit*: wie zijn deze mensen, wat verbindt hen
- **Nooit**: "fyp, viral, nederland" als naam. **Wel**: "Gewoon Nederlands TikTok" of "Dutch Muslim Comedy Creators"
- Prompt-instructie aan Claude: geen hashtags in de naam, geen hashtag-achtige woorden
- Sub-clusters op elke diepte worden benoemd

### As-namen
- Alle PCA-assen krijgen een naam via `name_axes.py` (Claude API):
  - `name`: de naam van de as (max 5 woorden, Nederlands)
  - `rationale`: 1-2 zinnen over wat de as meet en waarom accounts hoog/laag scoren
  - `high_label`: label voor het hoge uiteinde (max 4 woorden)
  - `low_label`: label voor het lage uiteinde (max 4 woorden)
- Als Claude nog niet gedraaid heeft: gebruik correlatie met hand-crafted assen als fallback (r ≥ 0.65)
- Als ook dat niet beschikbaar: toon `PC5 (nog niet benoemd)` — een eerlijk placeholder, nooit hashtags
- **Pole labels op de plot**: altijd `high_label`/`low_label`, nooit de top-hashtags uit de loadings
- **Axis pills in de topbar**: altijd de naam, nooit "PC5 · vvd → fyp"

### Hand-crafted assen (bestaand systeem)
- `axis_scorer.py` berekent scores voor theoretische assen: Links–Rechts, Institutioneel vertrouwen, Religieuze identiteit, Cultureel vs. politiek, Bubbel-geslotenheid, Bereik
- Deze zijn interpretatief het sterkst maar handmatig gedefinieerd
- Correlaties tussen PCA-assen en hand-crafted assen zijn *context*, niet de naam zelf

---

## 5. Assen — Gedrag & Filosofie

### Interpretatief interessant
- De beste as is niet degene met de meeste globale variantie (standaard PCA)
- De beste as is degene die clusters het meest van elkaar *onderscheidt* — gemeten in eta² (ANOVA over cluster-lidmaatschap)
- Dit is interpretatief interessanter: het laat zien welke dimensies verantwoordelijk zijn voor de scheiding tussen bubbels

### Laag-adaptieve assen
- Op **macro-niveau**: de 2 globale PCs met hoogste eta² over macro-clusters
- Na **inzoomen op een cluster**: lokale PCA op alleen de accounts in dat cluster
  - Nieuwe TF-IDF matrix op die subset
  - Eigen PCs berekend
  - De 2 lokale PCs met hoogste eta² over sub-clusters worden automatisch gekozen
  - Sub-cluster posities opgeslagen als genormaliseerde waarden (0-1) in `pca_axes.json` onder `local_pca[cluster_id]`
- De assen veranderen dus *elke keer als je een laag dieper gaat*
- Dit laat zien: "wat maakt de sub-bubbels binnen deze bubbel anders van elkaar?"

### Correlatie als extra context
- In de sidebar staat bij elke as de sterkste correlatie met een hand-crafted as
- Bijv. "↔ Cultureel vs. politiek r=+0.87"
- Dit helpt de onderzoeker de statistische as te duiden zonder dat het de naam wordt

---

## 6. Visualisatie (v4)

### Algemeen gevoel
- Donker, minimalistisch, geen UI-rommel
- Animaties zijn purposeful — ze communiceren structuur, niet decoratie
- Het moet voelen alsof de tool leeft en evolueert

### Macro-view
- 3–5 grote bubbels gepositioneerd op de 2 meest onderscheidende globale assen
- Grootte van de bubbel = aantal accounts (sqrt-geschaald)
- Klikken op een bubbel = die bubbel "popt"

### Pop-animatie
- Sub-bubbels beginnen op de exacte positie van de parent-bubbel
- Ze animeren vloeiend naar hun lokale PCA-positie
- **De parent is het middelpunt** — sub-bubbels zijn offsets relatief aan de parent, niet absolute canvas-coördinaten
- Dit zorgt ervoor dat de parent altijd in het centrum van zijn kinderen staat
- Andere macro-bubbels blijven zichtbaar maar gedimmed op de achtergrond

### Navigatie
- **Unlimited lagen**: je kunt in elke sub-bubbel inzoomen als die sub-sub-clusters heeft
- **State als stack**: `state.path = []` is macro, `['1']` is in cluster 1, `['1', '1b']` is dieper
- **Breadcrumb**: toont de volledige navigatie-pad, elke stap is klikbaar
- **Back-knop**: gaat één laag terug
- **Klikken op gedimde achtergrond-bubbel**: navigeert naar die bubbel (eerst collapse, dan pop)

### Filters
- Sidebar toont filter-chips voor alle clusters op het huidige niveau
- Klikken op een chip: toggle zichtbaarheid
- Verborgen clusters zijn gedimmed maar nog wel zichtbaar (zodat je de structuur ziet)

### Sidebar
- **Actieve assen**: naam, eta², hi/lo labels, rationale (als beschikbaar), correlatie met hand-crafted as
- **Filter-chips**: per cluster op het huidige niveau
- **Cluster-info**: naam en beschrijving van het actieve cluster bij inzoomen
- Nooit hashtags als label in de sidebar

### Tooltip bij hover
- Naam van het cluster, aantal accounts, totaal bereik (volgers)
- Hashtags mogen hier WEL als contextuele info — je hoverd er bewust overheen
- "Klik om in te zoomen →" hint als het cluster sub-clusters heeft

---

## 7. Pipeline

Volledig automatisch na `python3 pipeline.py`:

1. **Collection** — scrape accounts en video's
2. **Hashtag tracker** — welke tags groeien, welke zijn nieuw
3. **Sound tracker** — originele geluiden als bubbel-signaal
4. **Bubble analysis** — Louvain + variance-based recursion → graph.json
5. **Auto-name clusters** — `name_clusters.py` via Claude API
6. **Visualization** — `generate_viz.py` voor v2
7. **Axis scores** — `axis_scorer.py` hand-crafted assen
8. **PCA axes** — `pca_axes.py` data-driven assen + lokale PCA per cluster
9. **Auto-name axes** — `name_axes.py` via Claude API *(toe te voegen aan pipeline)*
10. **History snapshot** — tijdreeks van cluster-groei
11. **Strategy rapport** — seeds voor volgende run

De handmatige stap na de pipeline:
- Bekijk strategy.py output
- Beslis welke bridge-accounts en hashtags veelbelovend zijn
- Voeg toe aan SEED_ACCOUNTS / SEED_HASHTAGS in config.py
- Kies: expansion run of bootstrap

---

## 8. Technische principes

- **Geen hardcode in visualisaties.** Altijd `fetch()` naar JSON, nooit cluster-data in de JS.
- **Eén databron.** `bubblescape.db` is de enige bron van waarheid. Alle JSON-exports zijn afgeleid.
- **Fingerprint-systeem voor stabiliteit.** Namen zijn gekoppeld aan hashtag-fingerprints, niet aan cluster-indices (die veranderen na Louvain).
- **Claude API voor naming.** `name_clusters.py --all` herbenoemt alles. `name_axes.py` benoemt PCA-assen. Beide draaien automatisch in de pipeline als `ANTHROPIC_API_KEY` beschikbaar is.
- **Credits bewust besteden.** Pre-check op bekende volgers, strategische seeds, MAX_VIDEO_PAGES als bewuste keuze.
- **Nooit committen:** `.env`, `data/bubblescape.db`, API keys.

---

## 9. Wat er nog op de wenslijst staat (nog niet gebouwd)

- **User-selecteerbare assen in v4**: standaard de 2 beste, maar de user kan switchen naar andere PCs of hand-crafted assen via een dropdown
- **Tijdreeks-view**: hoe zijn bubbels gegroeid en gesplitst over runs heen (history_append.py verzamelt al data)
- **Ondertitels/transcripties**: KonbiniAPI ondersteunt dit nog niet — feature request uitstaan
- **Meer runs**: huidige dataset is 315 accounts, 47K video's — nog relatief klein voor stabiele clusters
- **GitHub updaten**: repo heeft nog de oude Instagram-versie
- **name_axes.py toevoegen aan pipeline.py** als stap na pca_axes.py
