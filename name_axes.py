"""
name_axes.py — Geeft elke PCA-as een betekenisvolle naam + korte onderbouwing via Claude API.

Leest pca_axes.json, stuurt per as de top-hashtags + correlaties naar Claude,
en schrijft de namen terug naar pca_axes.json onder 'pc_names'.

Gebruik:
  python3 name_axes.py           # alleen nog niet benoemde assen
  python3 name_axes.py --all     # herbenoem alle assen
"""

import json
import sys
from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv(override=True)

BASE      = Path(__file__).parent
PCA_PATH  = BASE / "data" / "pca_axes.json"

HAND_CRAFTED_NAMES = {
    "political_lr":          "Links–Rechts spectrum",
    "institutional_trust":   "Institutioneel vertrouwen",
    "religious_identity":    "Religieuze identiteit",
    "cultural_vs_political": "Cultureel vs. politiek",
    "bubble_closure":        "Bubbel-geslotenheid",
    "reach":                 "Bereik",
}


def build_prompt(pc_key: str, loadings: dict, eta2: float, corr: dict) -> str:
    pos_tags = loadings.get("positive", [])[:10]
    neg_tags = loadings.get("negative", [])[:10]

    corr_lines = []
    for axis, vals in sorted(corr.items(), key=lambda x: -abs(x[1].get("r", 0))):
        r = vals.get("r", 0)
        name = HAND_CRAFTED_NAMES.get(axis, axis)
        corr_lines.append(f"  - {name}: r={r:+.2f}")
    corr_str = "\n".join(corr_lines) if corr_lines else "  (geen significante correlaties)"

    return f"""Je analyseert een dimensie (principale component) uit een TF-IDF + PCA analyse van Nederlandse TikTok-accounts.

Dimensie: {pc_key}
Verklaart cluster-scheiding (eta²): {eta2:.3f}

Hashtags die HOOG scoren op deze as (rechterkant / hoge waarde):
{", ".join(f"#{t}" for t in pos_tags)}

Hashtags die LAAG scoren op deze as (linkerkant / lage waarde):
{", ".join(f"#{t}" for t in neg_tags)}

Correlaties met theoretische assen:
{corr_str}

Geef een bondige analytische naam voor deze dimensie (max 5 woorden, Nederlands) en een onderbouwing van 1-2 zinnen die uitlegt wat de as meet en waarom accounts hoog of laag scoren.

Antwoord ALLEEN in dit JSON-formaat (geen markdown, geen uitleg erbuiten):
{{"name": "...", "rationale": "...", "high_label": "...", "low_label": "..."}}

Waarbij:
- name: de naam van de as (max 5 woorden)
- rationale: 1-2 zinnen over wat de as meet
- high_label: korte label voor het hoge uiteinde (max 4 woorden)
- low_label: korte label voor het lage uiteinde (max 4 woorden)"""


def name_axis(client, pc_key: str, loadings: dict, eta2: float, corr: dict) -> dict:
    prompt = build_prompt(pc_key, loadings, eta2, corr)
    msg = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=256,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def main():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        print("Geen ANTHROPIC_API_KEY gevonden in .env — stop.")
        sys.exit(1)

    import anthropic
    client = anthropic.Anthropic(api_key=api_key)

    pca = json.loads(PCA_PATH.read_text())
    loadings  = pca.get("hashtag_loadings", {})
    eta2_map  = pca.get("cluster_separation_eta2", {})
    corr_map  = pca.get("correlations_with_handcrafted", {})

    existing = pca.get("pc_names", {})
    force    = "--all" in sys.argv

    updated = dict(existing)
    pcs = sorted(loadings.keys(), key=lambda k: -eta2_map.get(k, 0))

    for pc in pcs:
        if pc in existing and not force:
            print(f"  {pc}: al benoemd → \"{existing[pc]['name']}\" (skip)")
            continue
        eta2 = eta2_map.get(pc, 0)
        corr = corr_map.get(pc, {})
        print(f"  Benoem {pc} (eta²={eta2:.3f}) …", end=" ", flush=True)
        try:
            result = name_axis(client, pc, loadings[pc], eta2, corr)
            updated[pc] = result
            print(f"→ \"{result['name']}\"")
        except Exception as e:
            print(f"FOUT: {e}")

    pca["pc_names"] = updated
    PCA_PATH.write_text(json.dumps(pca, indent=2, ensure_ascii=False))
    print(f"\nOpgeslagen → {PCA_PATH}")
    print("\nBenoemde assen:")
    for pc in pcs:
        if pc in updated:
            n = updated[pc]
            print(f"  {pc}: {n['name']}")
            print(f"       ↑ {n['high_label']}  ↓ {n['low_label']}")
            print(f"       {n['rationale']}")


if __name__ == "__main__":
    main()
