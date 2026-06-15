/**
 * bubble_finder.js — Bubblescape v2
 * AI-powered search and hypothesis validation.
 * Exposes window.BubbleFinder — loaded as classic script.
 *
 * Two modes:
 *   MATCH      — describe a group of people → find matching bubble
 *   HYPOTHESE  — pose a claim about a bubble → validate with data
 *
 * Bilingual: pass lang = 'nl' | 'en' to findBubble().
 * Direct browser call to Anthropic Messages API.
 */

window.BubbleFinder = (function () {

  const ANTHROPIC_API = "https://api.anthropic.com/v1/messages";
  const MODEL = "claude-sonnet-4-20250514";

  // ── API key management ─────────────────────────────────────────────────────

  function getApiKey() {
    const stored = sessionStorage.getItem("bf_anthropic_key");
    if (stored) return stored;
    const meta = document.querySelector('meta[name="anthropic-key"]');
    return (meta && meta.content) ? meta.content.trim() : "";
  }

  function setApiKey(key) { sessionStorage.setItem("bf_anthropic_key", key.trim()); }
  function clearApiKey() { sessionStorage.removeItem("bf_anthropic_key"); }
  function hasKey() { return Boolean(getApiKey()); }

  // ── Query mode detection ───────────────────────────────────────────────────

  const HYPOTHESE_SIGNALS_NL = [
    /\bbestaat\b/i, /\bklopt\b/i, /\bis het zo dat\b/i, /\bzou\b.*\b(bubbel|cluster)\b/i,
    /\bcombinatie\b/i, /\btegelijk(ertijd)?\b/i, /\b(én|zowel).+\bals\b/i,
    /\?$/, /\bwaar of niet\b/i, /\bsamen\b.+\bbubbel\b/i, /\bblijken\b/i,
    /\bveronderstelling\b/i, /\bthese\b/i, /\bhypothese\b/i,
    /\bdie ook\b/i, /\bmaar ook\b/i, /\ben ook\b/i, /\btegelijk\b/i,
  ];

  const HYPOTHESE_SIGNALS_EN = [
    /\bexist[s]?\b.*\bbubble\b/i, /\bis it true\b/i, /\bdo.*both\b/i,
    /\bsame bubble\b/i, /\?$/, /\bhypothesis\b/i, /\bsuppose\b/i,
    /\bwho also\b/i, /\bbut also\b/i, /\band also\b/i, /\bat the same time\b/i,
    /\bcombination\b/i, /\bcoexist\b/i, /\boverlap\b/i,
  ];

  function detectMode(query, lang = 'nl') {
    const signals = lang === 'en' ? [...HYPOTHESE_SIGNALS_NL, ...HYPOTHESE_SIGNALS_EN] : HYPOTHESE_SIGNALS_NL;
    return signals.some(re => re.test(query)) ? "hypothese" : "match";
  }

  // ── Cluster summary builder ────────────────────────────────────────────────

  function buildClusterSummary(data) {
    return data.clusters.map(c => ({
      id: c.id,
      name: c.name,
      description: c.description || null,
      top_hashtags: (c.top_hashtags || []).slice(0, 8),
      size: c.size,
      density: c.density,
      reach: c.reach,
      bridge_accounts: (c.bridge_accounts || []).slice(0, 3).map(b => b.username),
      subclusters: (c.subclusters || []).map(sc => ({
        id: sc.id,
        name: sc.name || sc.id,
        top_hashtags: (sc.top_hashtags || []).slice(0, 6),
        density: sc.density,
        size: sc.size,
        bridge_accounts: (sc.accounts || [])
          .filter(a => a.is_bridge).slice(0, 3).map(a => a.username),
      })),
    }));
  }

  // ── MATCH system prompts ───────────────────────────────────────────────────

  const MATCH_SYSTEM_NL = `Je bent een onderzoeker van informatiebubbels op Nederlandse TikTok.
Je krijgt een beschrijving van een groep mensen en een dataset van clusters.
De clusters zijn gedetecteerd via Louvain op hashtag-co-gebruik — ze representeren échte informatie-ecosystemen.

BELANGRIJK: Wees eerlijk over wat de data WEL en NIET bevat.
- Als de beschrijving meerdere aspecten combineert die in VERSCHILLENDE clusters zitten, zeg dat dan.
- Als een aspect van de beschrijving GEEN enkel cluster in de data heeft, zeg dat dan expliciet.
- Hoge confidence (>0.7) betekent: ALLE aspecten van de beschrijving passen bij één cluster.

Reageer ALLEEN met geldig JSON, zonder markdown code blocks:
{
  "mode": "match",
  "spans_multiple": false,
  "missing_aspects": [],
  "partial_matches": [],
  "matches": [
    {
      "cluster_id": "1",
      "cluster_name": "...",
      "matched_subcluster_id": "1a",
      "matched_subcluster_name": "...",
      "confidence": 0.87,
      "why": "Één zin waarom deze cluster past — benoem welke aspecten WEL en NIET gevonden zijn.",
      "blind_spots": ["blind spot 1", "blind spot 2", "blind spot 3"],
      "bridge_accounts": ["@username1"]
    }
  ],
  "summary": "Twee zinnen. Als de combinatie niet bestaat als één bubbel, zeg dat."
}

Als de beschrijving meerdere aspecten combineert uit VERSCHILLENDE clusters:
- Zet spans_multiple: true
- Vul partial_matches in: { "aspect": "...", "cluster_id": "1", "subcluster_id": "1a", "cluster_name": "...", "subcluster_name": "...", "confidence": 0.85 }
- Voor ontbrekende aspecten: { "aspect": "...", "cluster_id": null, "exists": false }
- Zet matches: [] als confidence < 0.55

Als NIETS past: matches: [], spans_multiple: false, summary: korte uitleg.`;

  const MATCH_SYSTEM_EN = `You are a researcher of information bubbles on Dutch TikTok.
You receive a description of a group of people and a dataset of clusters.
The clusters were detected via Louvain on hashtag co-usage — they represent real information ecosystems.

IMPORTANT: Be honest about what the data DOES and DOES NOT contain.
- If the description combines multiple aspects from DIFFERENT clusters, say so.
- If an aspect of the description has NO matching cluster in the data, say so explicitly.
- High confidence (>0.7) means: ALL aspects of the description fit ONE cluster. If only one aspect fits, confidence is low.

Respond ONLY with valid JSON, without markdown code blocks:
{
  "mode": "match",
  "spans_multiple": false,
  "missing_aspects": [],
  "partial_matches": [],
  "matches": [
    {
      "cluster_id": "1",
      "cluster_name": "...",
      "matched_subcluster_id": "1a",
      "matched_subcluster_name": "...",
      "confidence": 0.87,
      "why": "One sentence why this cluster fits — mention which aspects ARE and ARE NOT found.",
      "blind_spots": ["concrete blind spot 1", "blind spot 2", "blind spot 3"],
      "bridge_accounts": ["@username1"]
    }
  ],
  "summary": "Two sentences. If the combination doesn't exist as one bubble, say so."
}

If the description combines multiple aspects from DIFFERENT clusters:
- Set spans_multiple: true
- Fill partial_matches: { "aspect": "...", "cluster_id": "1", "subcluster_id": "1a", "cluster_name": "...", "subcluster_name": "...", "confidence": 0.85 }
- For missing aspects: { "aspect": "...", "cluster_id": null, "exists": false }
- Set matches: [] if confidence < 0.55

If NOTHING fits: matches: [], spans_multiple: false, summary: brief explanation.`;

  // ── HYPOTHESE system prompts ───────────────────────────────────────────────

  const HYPOTHESE_SYSTEM_NL = `Je bent een onderzoeker van informatiebubbels op Nederlandse TikTok.
Je krijgt een hypothese over een groep mensen — een stelling die de gebruiker wil toetsen.
Je krijgt ook de werkelijke clusterdata (hashtag-patronen, dichtheden, sizes).

Jouw taak: evalueer of de hypothese klopt op basis van de data.

Denk in drie stappen:
1. Welke cluster(s) zouden de hypothese-groep moeten bevatten als de hypothese klopt?
2. Bestaat die combinatie als coherente bubbel in de data?
3. Wat zegt de data — BEVESTIGD, GEDEELTELIJK, of ONTKRACHT?

Wees eerlijk: als de data de hypothese niet ondersteunt, zeg dat.

Reageer ALLEEN met geldig JSON, zonder markdown code blocks:
{
  "mode": "hypothese",
  "verdict": "BEVESTIGD",
  "verdict_score": 0.82,
  "hypothese_samenvatting": "Korte herhaling van de hypothese in één zin.",
  "redenering": "Twee tot drie zinnen over HOE je tot dit verdict komt op basis van de data.",
  "bewijs": ["Concreet data-punt", "Nog een data-punt"],
  "nuance": "Eén zin over wat de hypothese mist of oversimplificateert.",
  "clusters_betrokken": [
    {
      "cluster_id": "2",
      "cluster_name": "...",
      "subcluster_id": "2a",
      "subcluster_name": "...",
      "relevantie": "Waarom dit cluster relevant is voor de hypothese."
    }
  ],
  "alternatieve_lezing": "Als de hypothese niet klopt: hoe zit het dan WEL? Eén zin."
}

Geldige verdicts: BEVESTIGD / GEDEELTELIJK / ONTKRACHT`;

  const HYPOTHESE_SYSTEM_EN = `You are a researcher of information bubbles on Dutch TikTok.
You receive a hypothesis about a group of people — a claim the user wants to test.
You also receive the actual cluster data (hashtag patterns, densities, sizes).

Your task: evaluate whether the hypothesis holds based on the data.

Think in three steps:
1. Which cluster(s) should contain the hypothesis group if the hypothesis is correct?
2. Does that combination exist as a coherent bubble in the data?
3. What does the data say — BEVESTIGD (confirmed), GEDEELTELIJK (partial), or ONTKRACHT (refuted)?

Be honest: if the data doesn't support the hypothesis, say so. If the combination is split across two separate bubbles, say so.

Respond ONLY with valid JSON, without markdown code blocks:
{
  "mode": "hypothese",
  "verdict": "BEVESTIGD",
  "verdict_score": 0.82,
  "hypothese_samenvatting": "Short restatement of the hypothesis in one sentence.",
  "redenering": "Two to three sentences about HOW you reach this verdict based on the data.",
  "bewijs": ["Concrete data point supporting or refuting the hypothesis", "Another data point"],
  "nuance": "One sentence about what the hypothesis misses or oversimplifies.",
  "clusters_betrokken": [
    {
      "cluster_id": "2",
      "cluster_name": "...",
      "subcluster_id": "2a",
      "subcluster_name": "...",
      "relevantie": "Why this cluster is relevant for the hypothesis."
    }
  ],
  "alternatieve_lezing": "If the hypothesis is wrong: how does it actually work? One sentence."
}

Use ONLY these verdict values: BEVESTIGD / GEDEELTELIJK / ONTKRACHT (so the UI can display them correctly).`;

  // ── Prompt builders ────────────────────────────────────────────────────────

  function buildMatchPrompt(query, data, lang) {
    const intro = lang === 'en'
      ? `Group description: "${query}"\n\nAvailable clusters:`
      : `Beschrijving van de groep: "${query}"\n\nBeschikbare clusters:`;
    return `${intro}\n${JSON.stringify(buildClusterSummary(data), null, 2)}`;
  }

  function buildHypothesePrompt(query, data, lang) {
    const intro = lang === 'en'
      ? `Hypothesis to test: "${query}"\n\nAvailable cluster data:`
      : `Hypothese om te toetsen: "${query}"\n\nBeschikbare clusterdata:`;
    return `${intro}\n${JSON.stringify(buildClusterSummary(data), null, 2)}`;
  }

  // ── API call ───────────────────────────────────────────────────────────────

  async function _callApi(system, userMessage) {
    const key = getApiKey();
    if (!key) {
      const err = new Error(
        window._BFLang === 'en' ? "No API key set." : "Geen API-sleutel ingesteld."
      );
      err.code = "NO_KEY";
      throw err;
    }

    const response = await fetch(ANTHROPIC_API, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": key,
        "anthropic-version": "2023-06-01",
        "anthropic-dangerous-direct-browser-access": "true",
      },
      body: JSON.stringify({
        model: MODEL,
        max_tokens: 1200,
        system,
        messages: [{ role: "user", content: userMessage }],
      }),
    });

    if (!response.ok) {
      let msg = `API error ${response.status}`;
      try {
        const err = await response.json();
        if (response.status === 401) {
          const e = new Error(
            window._BFLang === 'en' ? "Invalid API key." : "Ongeldige API-sleutel."
          );
          e.code = "INVALID_KEY";
          throw e;
        }
        msg = err?.error?.message || msg;
      } catch (inner) {
        if (inner.code) throw inner;
      }
      throw new Error(msg);
    }

    const result = await response.json();
    const raw = result.content[0].text.trim()
      .replace(/^```json\s*/i, "").replace(/^```\s*/, "").replace(/\s*```$/, "").trim();

    return JSON.parse(raw);
  }

  // ── Public: findBubble ─────────────────────────────────────────────────────

  async function findBubble(query, data, lang = 'nl') {
    window._BFLang = lang;
    const mode = detectMode(query, lang);
    if (mode === "hypothese") {
      const sys = lang === 'en' ? HYPOTHESE_SYSTEM_EN : HYPOTHESE_SYSTEM_NL;
      return _callApi(sys, buildHypothesePrompt(query, data, lang));
    } else {
      const sys = lang === 'en' ? MATCH_SYSTEM_EN : MATCH_SYSTEM_NL;
      return _callApi(sys, buildMatchPrompt(query, data, lang));
    }
  }

  // ── Public API ─────────────────────────────────────────────────────────────

  return { findBubble, detectMode, getApiKey, setApiKey, clearApiKey, hasKey };

})();
