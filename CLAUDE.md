# CLAUDE.md - Alejandro Shroom Map

Operational context for picking this project back up. The README says what the app does; this file is
the *why* behind non-obvious decisions. It inherits almost everything from the Anastasiia Plant Map, whose
CLAUDE.md explains the frontend and testing gotchas (DOM markers instead of a GeoJSON layer, the MapLibre
credit control, `?v=N` cache busting, the idempotent `ALTER TABLE ... IF NOT EXISTS` schema approach,
best-effort external calls that are never cached on failure). Read that first; it all still applies.

## State: deployed 2026-10-04 at https://shroommap.217-154-218-125.sslip.io/

Verified 2026-10-04: tests pass; local Docker run (GBIF, ecoregion, Nominatim live; add sighting; dated tree);
on the server one real Gemini identification (Amanita muscaria, 0.95, worst toxicity 5 via Death cap).
Tree data: `backend/data/fungi_tree.json.gz` from Varga et al. 2019 (Zenodo 5003983, CC0), FastDate replicate
222 (crown 437.8 Myr, the median of the 10 replicates), relabelled with the paper's Species_name_match.csv by
`scripts/relabel_varga.py`: 4,784 species. It covers Agaricomycetes only - morels, truffles and other
Ascomycota are unplaced (Open Tree fallback).

## Decisions

- Internal names kept (`leaves` table, `Leaf`, `/api/leaves`, `leaf-*` CSS). Only user-facing text changed.
- Ports: Rock 8010, Plant 8011, **this app 8012**. DB/user name `shroommap`. Frontend at
  `/var/www/html/alejandroshroommap/frontend/`. Hostname `shroommap.217-154-218-125.sslip.io` (placeholder
  following the other apps' pattern).
- Gemini: ONE key (40/day) shared by four apps, split from 2026-10-04 until further notice as
  Shroom 15, Animal 10, Plant 10, Rock 5 (`GEMINI_DAILY_LIMIT_PER_KEY` in each app's `.env`; this app: 15).
  Each app counts only its own calls, so the four values must keep summing to the key's real limit.
- The AI answer is never trusted as typed: names go through the GBIF backbone with the kingdom filter; a
  kingdom-only match counts as unresolved.
- The AI response flag is `is_fungus` / `is_animal` (was `is_plant`); the frontend reads the same key.
- Tree data is built by `scripts/build_tree.py` (stdlib only, replaces the Plant Map's R script). It reads
  Newick/Nexus, understands `Genus_species_FAMILY_ORDER` tip labels, and can join group trees with a
  backbone. TimeTree data must never be bundled.
- Tip names are matched exactly against GBIF's accepted names. There is no synonym handling yet; if
  too many species show `*`, add an alias table in the builder.

- Edibility / toxicity / confidence (2026-10-04, owner's requests): Gemini rates every candidate 0-5 from the
  literature (edibility, toxicity) plus identifiability (1-5, from photos) and similar_species (0-5).
  Shown, top to bottom: Edibility; Toxicity of the identified species itself (the owner did not want the
  worst case shown); Confidence = share x (identifiability/5) x (1 - similar_species/10), where share =
  this species' AI confidence / sum over it and the alternatives (`_reliability` in ai.py). Missing values
  fail safe (toxicity 5, edibility 0, identifiability 1, similar_species 5). The worst toxicity over
  alternatives + dangerous look-alikes is still computed and greys out the edibility score; look-alikes are
  listed with their toxicity. A "do not eat, have it checked by an expert" note (amber, per the owner) is
  on every popup. Keep the greying-out and the note.

## Constraints

- **Dated-tree coverage is thin** next to the plant tree (~5,000 species vs ~72,500), and fungal taxonomy changes a lot, so GBIF's accepted names often differ from the names in the tree. Many species will be placed at their genus (marked `*`), or fall to the Open Tree fallback.
- Fungal divergence times rest on few fossils: the app says dates are approximate.
- Identification from one photo is the weakest link of this app. Treat results as a hint.
