# Bubblescape — Visualization State
*Start every visualization chat with this document. For scraper/API context, see PROJECT_STATE.md.*
*For the research/paper track (critical-realist method, nested SBM, the v3 prototypes strata_view/emergence_view), see RESEARCH_NOTES.md.*

---

## Wat is Bubblescape?

Een onderzoekstool die algoritmische filterbubbels op Nederlandse TikTok in kaart brengt.

**Theoretische definitie (gebruik dit voor cluster-labels en academische context):**
> A bubble is a partially closed actual domain — a structured set of empirical exposures produced by the conjoint operation of algorithmic mechanisms, social structures, and individual agency. Bubbles are emergent properties, not purely psychological (confirmation bias) nor purely structural (algorithmic determinism). They persist because the structural mechanisms producing them are largely unobservable to the people inside them.

**Technisch:** bubbels = clusters in een hashtag-co-occurrence graaf. Geen follow-graaf (following lists zijn private op TikTok).

---

## Database

**Locatie:** `/Users/jesperboon/Documents/bubblescape/data/bubblescape.db`

**Huidige omvang (2026-06-12):**
- 4.530+ accounts totaal
- 2.400+ Nederlandse accounts (`is_dutch = 1`)
- 2.346 volledig gescraped (`fully_collected = 1`)
- 123.000+ videos

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
    hashtags TEXT,          -- JSON array: top hashtags voor dit account
    collected_at TEXT,
    fully_collected INTEGER, -- 1 = volledig gescraped
    is_dutch INTEGER        -- 1 = Nederlands, 0 = non-Dutch (uitgesloten), NULL = onbekend
)

videos (
    video_id TEXT PRIMARY KEY,
    author TEXT,            -- username van de maker
    description TEXT,       -- caption (plain tekst)
    hashtags TEXT,          -- JSON array van hashtags in deze video
    audio_id TEXT,
    audio_title TEXT,
    likes INTEGER,
    comments INTEGER,
    views INTEGER,
    collected_at TEXT
)
```

---

## Hoe de graaf gebouwd moet worden

### Stap 1: Hashtag co-occurrence matrix

```python
# Loop over alle videos van fully_collected Dutch accounts
# Voor elk paar hashtags in dezelfde video → edge met gewicht +1
SELECT v.hashtags, a.username, a.followers
FROM videos v
JOIN accounts a ON v.author = a.username
WHERE a.is_dutch = 1 AND a.fully_collected = 1
AND v.hashtags != '[]' AND v.hashtags IS NOT NULL
```

### Stap 2: Filters (cruciaal voor clusterstructuur)

**Verwijder generieke hashtags** die in >40% van accounts voorkomen — ze verbinden alles met alles en vernietigen de clusterstructuur:
```
fyp, foryou, viral, voorjou, foryoupage, fy, parati, trending, capcut,
voorjoupagina, fvd, pvv, fypシ, fyppppppppppppppppppppppp, tiktok
```

**Minimum drempel:** verwijder hashtags die in <3 accounts voorkomen (ruis).

### Stap 3: Community detection

- Gebruik **Louvain** community detection (`pip install python-louvain networkx`)
- Altijd opnieuw berekenen vanuit de ruwe DB — nooit cachen
- Verwacht: 8-15 betekenisvolle clusters

### Stap 4: Account → community mapping

Elk account krijgt de community van zijn meest gebruikte hashtag-cluster.
Accounts zonder genoeg hashtag-data → "Overig" groep.

---

## Bekende clusters (op basis van corpus-analyse)

Gebaseerd op top hashtags in 123k+ videos van 2.346 Nederlandse accounts:

| Cluster | Kernhashtags | Verwacht formaat |
|---|---|---|
| 🏛️ Politiek | #politiek, #tweedekamer, #verkiezingen, #debat, #d66, #vvd, #denhaag | Groot — politiek is entry point van dataset |
| 🤣 Humor & Memes | #grappig, #humor, #bankzitters, #herkenbaar, #lachen, #comedy, #newkids | Groot — mainstream NL humor |
| 🎵 Muziek & Joost | #joostklein, #joost, #nlrap, #muziek, #rap | Middelgroot |
| 📰 Nieuws & Media | #nieuws, #robjetten, #podcast, #politics | Middelgroot |
| 🏫 Onderwijs | #onderwijs, #school, #juf, #teacher | Niche maar duidelijk |
| 🌍 Steden & Regio | #amsterdam, #rotterdam, #utrecht, #denhaag | Geografisch verspreid |
| 🕌 Islamitisch | #islam, #islamicreminder | Niche, duidelijk afgebakend |
| 🍳 Lifestyle & Koken | #schoonmaken, #cleantok, #recept, #vlog | Groeiend door HypeAuditor bootstrap |
| ⚽ Voetbal | #voetbal, #football, #eredivisie, #fcutrecht, #liverpool | Problematisch — deels internationaal |
| 🏛️ Geschiedenis | #nostalgie, #nostalgia, #geschiedenis, #studio100 | Niche, interessant |

**Let op:** `#liverpool` staat hoog in het corpus (490x). Dit is een bekende contaminatie — Nederlandse voetbalfans die over Liverpool praten. Verwijder `liverpool`, `premier league`, `bundesliga` als stopwoorden in de graafbouw.

---

## Visualisatie-eisen

### Must-have
- Force-directed layout (D3.js of vis.js network)
- Elke **node = één account**
- **Node-grootte** = `log10(followers)` — grote accounts vallen op maar domineren niet
- **Node-kleur** = community/bubbel
- **Hover** → username, followers, top-3 hashtags, community-label
- **Legenda** met community-namen (automatisch gegenereerd op basis van top hashtags per community)
- **Zoekbalk** om account te highlighten

### Nice-to-have
- Klikken op node → toont verbonden accounts in sidebar
- Filter op minimum followers (slider)
- Community filter (toon alleen bubbel X)
- Export PNG

### Design
- Donkere achtergrond (`#0f0f1a`)
- Levendige cluster-kleuren (goed contrast op donker)
- Minimalistisch, research-esthetiek
- Responsive (werkt op laptop-scherm)

---

## Output

**Één enkel bestand:** `/Users/jesperboon/Documents/bubblescape/visualisatie.html`

- Volledig standalone (geen server nodig)
- Data als inline JSON in de HTML
- Werkt door simpelweg te openen in een browser

---

## Technische aanpak (aanbevolen)

**Stap 1: Python-script** dat:
1. DB leest en hashtag co-occurrence berekent
2. Louvain draait
3. Community-labels genereert (top-3 hashtags per community)
4. Nodes + edges exporteert als JSON

**Stap 2: HTML/JS** met D3.js force simulation
- Data als inline `<script>` JSON
- Alle interactiviteit in vanilla JS + D3

**Test eerst** met een subset van 200 accounts om de layout te valideren voordat je alles rendert.

---

## Doelgroep

Academisch publiek (scriptiebegeleiders, onderzoekers), maar ook begrijpelijk voor journalisten en beleidsmakers. De visualisatie is primair voor exploratie en presentatie — niet voor statistische analyse.
