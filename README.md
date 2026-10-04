# Alejandro Shroom Map

**Live app: https://shroommap.217-154-218-125.sslip.io/** · Code: https://github.com/drayac/AlejandroShroomMap

A mobile-first web app for logging mushrooms and other fungi you find: photo + GPS pin, Gemini proposes a species, GBIF checks the name, RESOLVE gives the ecosystem, and the species you collect are placed on a dated tree of life.

Sibling of the Anastasiia Plant Map and Rock Map: same look, same features, a different subject.

## What differs from the Plant Map

- Identification uses a mycologist prompt. It is told to be conservative (a photo cannot show spore print, smell or flesh colour change) and to prefer genus level when unsure.
- Each identification shows three 0-5 scores: **Edibility**, **Toxicity** (of the identified species) and **Confidence** (the species' share of the AI's candidates, discounted for species that are hard to identify from photos or have many look-alikes). Poisonous look-alikes are listed, edibility is greyed out when a poisonous match is possible, and every mushroom carries a "do not eat based on this app, have it checked by an expert" note. Never eat a mushroom because of this app.
- GBIF lookups use kingdom Fungi (backbone key 5) both for name matching and for "fungi recorded near here".
- The Open Tree of Life fallback is queried in the "Fungi" TNRS context.
- The dated tree covers **mushroom-forming fungi (Agaricomycetes) only** (see DATA.md). Other fungi (morels, truffles, molds, yeasts, most lichens) fall back to the undated Open Tree cladogram, exactly as mosses do in the Plant Map.

Everything else is unchanged: add flow (photo, GPS or map pin, optional Gemini identification with
alternatives), map with round photo markers and five base styles, gallery by country and month, the
ecoregion lookup (RESOLVE Ecoregions 2017), the tree view, passphrase-gated adding, the Gemini credit
counter.

## Constraints and known weak spots

- **Dated-tree coverage is thin** next to the plant tree (~5,000 species vs ~72,500), and fungal taxonomy changes a lot, so GBIF's accepted names often differ from the names in the tree. Many species will be placed at their genus (marked `*`), or fall to the Open Tree fallback.
- Fungal divergence times rest on few fossils: the app says dates are approximate.
- Identification from one photo is the weakest link of this app. Treat results as a hint.

## The tree

Source order: dated tree (`backend/dated_tree.py`, from `backend/data/fungi_tree.json.gz`) -> Open Tree
of Life (topology only) -> Linnaean classification. Species missing from the dated tree are attached at
the root of their genus (or family) and marked `*`. The data file is in the repo (built from Varga et al. 2019,
4,784 species; see `DATA.md` to rebuild it).

## Stack and layout

FastAPI + PostgreSQL/PostGIS in Docker Compose (Pillow for photos), a plain HTML/CSS/JS frontend on
MapLibre GL + OpenFreeMap (no build step). Copied from the Anastasiia Plant Map (release 0.1.0) and
reworked; it is a completely separate app (own database, port, hostname, uploads, Gemini counter).

```
backend/   main.py ai.py taxonomy.py ecology.py tree.py dated_tree.py models.py config.py db.py
           test_trees.py  data/fungi_tree.json.gz   <- built by scripts/build_tree.py (see DATA.md)
frontend/  index.html app.js style.css manifest.json icon.svg
scripts/   build_tree.py  test_build_tree.py
deploy/nginx.conf   docker-compose.yml   .env.example   DEPLOY.md   DATA.md   CLAUDE.md
```

Internal identifiers were deliberately NOT renamed: the table is `leaves`, the model `Leaf`, the routes
`/api/leaves` and `/api/identify`, CSS classes `leaf-*`. Only user-facing text, prompts, GBIF filters,
names, ports and keys changed. This keeps the code diffable against the Plant Map; do not "clean it up".

## Local development

```bash
cp .env.example .env     # passphrase, DB password, optional Gemini key
docker compose up -d --build
curl http://localhost:8012/api/health
python scripts/test_build_tree.py && (cd backend && python test_trees.py)   # offline tests
```

Serve `frontend/` behind a reverse proxy that sends `/api/` and `/uploads/` to `localhost:8012`
(`deploy/nginx.conf`). HTTPS is required for GPS on anything but localhost. After editing `app.js` or
`style.css`, bump `?v=N` in `frontend/index.html`.

Deploying: see `DEPLOY.md`.

## Data and attribution

- Map tiles: OpenFreeMap, (c) OpenMapTiles, data (c) OpenStreetMap contributors
- Species records and taxonomy: [GBIF](https://www.gbif.org)
- Ecosystems: RESOLVE Ecoregions 2017 (Dinerstein et al. 2017, *BioScience* 67:534, CC BY 4.0)
- Divergence times: Varga et al. (2019), *Nature Ecology & Evolution* 3:668, Agaricomycetes megaphylogeny (data on Zenodo, record 5003983). Check the record's licence before committing the converted tree file.
- Tree topology fallback: [Open Tree of Life](https://opentreeoflife.org) (CC0)
- Identification: Google Gemini
