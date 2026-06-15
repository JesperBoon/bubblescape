# Strata View — Data Contract

*The JSON shape the model team must produce so `v3/strata_view.html` can render real
data instead of the mock fixture.*

The strata view renders the output of a **bipartite nested block model**. It is built
around a three-layer ontology, and every field below maps onto one of those layers:

| Layer | In the data | In the view |
|---|---|---|
| **REAL** — the whole space, everything that exists | implicit: the universe of accounts | the outer faint/dashed field |
| **ACTUAL** — patterns the model *infers* | `blocks` (kind `account`) | semi-transparent coloured regions |
| **EMPIRICAL** — what we actually observe | `accounts` + `members` | the dots |

The tool maps **who posts what, not who sees what.** The data measures content
*producers*, not viewers. Keep that honesty in the data: emit `null` / omit rather than
inventing structure where the model is uncertain.

---

## Top-level object

```jsonc
{
  "recommended_level": 1,        // int. Sharpest scale from the effective-information /
                                 //   causal-emergence measure. The view DEFAULTS here on load.
                                 //   Must equal a `level` that exists in `blocks`.

  "blocks":   [ /* Block[] */ ], // the nested block model — both sides of the bipartite graph
  "couplings":[ /* Coupling[] */ ], // account-block <-> content-block links (the coupling view)
  "overlaps": [ /* Overlap[] */ ],  // explicit shared-account bridges between two blocks
  "accounts": { /* { [username]: Account } */ } // per-account metadata for the detail panel
}
```

---

## `Block`

A node in the nested block model. **Account-blocks are the bubbles.** Content-blocks
exist only to drive the coupling (bipartite) view.

```jsonc
{
  "id": "b1",                    // string, unique across all blocks
  "level": 0,                    // int nesting depth. 0 = macro. Children are level+1.
  "parent": null,                // parent block id, or null for a top-level block.
                                 //   Parent/child must be same `kind`.
  "kind": "account",             // "account" | "content" — which side of the bipartite model.
                                 //   "account" blocks are drawn as bubbles. "content" blocks
                                 //   are the right-hand column in the coupling view.

  "label": "Politically engaged NL",   // string OR { "en": "...", "nl": "..." } for the
                                        //   EN/NL toggle. A bare string is shown in both.

  "content_regime": ["politiek", "tweedekamer", "pvv"],
                                 // dominant hashtags/sounds for this block. DRIVES THE COLOUR:
                                 //   colour is a pure function of content_regime, never random.
                                 //   May be [] -> the block renders grey ("unknown regime").

  "reach": 184000,              // int|null. Audience reach (sum of followers, etc). null = unknown.
  "persistence": 0.85,          // float 0..1 | null. How durable the structure is across
                                 //   snapshots. LOW (<0.4) -> the bubble is dimmed + dashed
                                 //   ("momentary"). null = unknown -> rendered neutral, no claim.

  "members": [                  // soft membership. OVERLAP IS ALLOWED: an account may appear in
    { "account": "username1", "membership": 0.92 },   // the members of several blocks at once.
    { "account": "username2", "membership": 0.55 }    // membership 0..1. Position + opacity of
  ]                                                    //   the dot reflect this number.
}
```

Notes for the model team:

- **Soft membership is the whole point.** Do **not** hard-assign each account to one
  block. An account split 70/30 between two blocks must carry *both* memberships
  (`0.7` and `0.3`); the renderer places its dot between the two bubbles and frays it at
  the boundary. A crisp 1.0 sits dense at the centre.
- An account whose memberships are all weak (max `< 0.30`) is treated as an **"I don't
  know"** point: scattered faintly in the REAL field, *outside* any bubble. This is
  intended — emit weak memberships rather than forcing a confident assignment.
- `content_regime[0]` is used as the primary colour key. Keep the most characteristic
  tag first.

---

## `Coupling`

One edge of the bipartite model: an account-block coupled to a content-block. In the
coupling view, **the band between the two columns *is* the bubble.**

```jsonc
{
  "account_block_id": "b1",   // id of a kind:"account" block (left column)
  "content_block_id": "c2",   // id of a kind:"content" block (right column)
  "weight": 0.8               // float 0..1 — band thickness + opacity
}
```

---

## `Overlap`

An explicit bridge: the set of accounts shared between two blocks. The renderer also
infers bridges from soft membership, but listing them here guarantees the shared
accounts are highlighted in the overlap "lens" (rendered amber, on top).

```jsonc
{
  "between": ["b1", "b2"],                 // exactly two block ids
  "shared_accounts": ["username2", "..."]  // usernames present in both
}
```

---

## `Account`

Per-account metadata for the click-through detail panel. Keyed by username.

```jsonc
"username2": {
  "followers": 41200,                       // int|null
  "verified": false,                        // bool
  "top_hashtags": ["politiek", "pvv"],      // string[] (may be [])
  "top_sounds": ["original sound - x"]      // string[] | null. null -> panel shows
                                            //   "unknown — no sound data", never blank-faked.
}
```

The account's **membership profile** (its spread across bubbles) is not stored here —
the renderer derives it from every `block.members` entry that references the username.

---

## Minimal example

```jsonc
{
  "recommended_level": 1,
  "blocks": [
    { "id": "b1", "level": 0, "parent": null, "kind": "account",
      "label": { "en": "Politically engaged NL", "nl": "Politiek betrokken NL" },
      "content_regime": ["politiek", "tweedekamer"], "reach": 184000, "persistence": 0.85,
      "members": [ { "account": "alice", "membership": 0.9 }, { "account": "bob", "membership": 0.5 } ] },
    { "id": "b1a", "level": 1, "parent": "b1", "kind": "account",
      "label": "Right-populist", "content_regime": ["pvv", "fvd", "migratie"],
      "reach": 96000, "persistence": 0.9,
      "members": [ { "account": "bob", "membership": 0.6 } ] },
    { "id": "c1", "level": 0, "parent": null, "kind": "content",
      "label": "#politiek sphere", "content_regime": ["politiek"], "reach": null, "persistence": null,
      "members": [] }
  ],
  "couplings": [ { "account_block_id": "b1", "content_block_id": "c1", "weight": 0.8 } ],
  "overlaps":  [ { "between": ["b1", "b1a"], "shared_accounts": ["bob"] } ],
  "accounts": {
    "alice": { "followers": 12000, "verified": true,  "top_hashtags": ["politiek"], "top_sounds": ["original sound"] },
    "bob":   { "followers": 41200, "verified": false, "top_hashtags": ["pvv","politiek"], "top_sounds": null }
  }
}
```

## Plugging in real data

`strata_view.html` currently builds its fixture in `buildMock()`. To go live, replace
the body of `loadData()` so it returns a parsed object of this exact shape — e.g.
`return await (await fetch('/data/strata.json')).json();` — and emit that file from the
model export (analogous to `bubbletree/bubbletree_export.py`). No other renderer changes
are required as long as the contract above holds.
