import base64
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

import ecology
import taxonomy
import shared_credits
from config import settings
from models import AiUsage

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

RESPONSE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "is_fungus": {"type": "BOOLEAN"},
        "scientific_name": {"type": "STRING"},
        "common_name": {"type": "STRING"},
        "confidence": {"type": "NUMBER"},
        "alternatives": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "scientific_name": {"type": "STRING"},
                    "common_name": {"type": "STRING"},
                    "confidence": {"type": "NUMBER"},
                    "edibility": {"type": "INTEGER"},
                    "toxicity": {"type": "INTEGER"},
                    "identifiability": {"type": "INTEGER"},
                    "similar_species": {"type": "INTEGER"},
                },
                "required": ["scientific_name", "common_name", "confidence", "edibility", "toxicity",
                             "identifiability", "similar_species"],
            },
        },
        "features": {"type": "STRING"},
        "notes": {"type": "STRING"},
        "edibility": {"type": "INTEGER"},
        "toxicity": {"type": "INTEGER"},
        "identifiability": {"type": "INTEGER"},
        "similar_species": {"type": "INTEGER"},
        "safety_notes": {"type": "STRING"},
        "dangerous_lookalikes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "scientific_name": {"type": "STRING"},
                    "common_name": {"type": "STRING"},
                    "toxicity": {"type": "INTEGER"},
                },
                "required": ["scientific_name", "common_name", "toxicity"],
            },
        },
    },
    "required": [
        "is_fungus", "scientific_name", "common_name", "confidence",
        "alternatives", "features", "notes",
        "edibility", "toxicity", "identifiability", "similar_species", "safety_notes", "dangerous_lookalikes",
    ],
}

# 0-5 scales, the same wording in the prompt and in the app (frontend/app.js).
EDIBILITY_SCALE = """0 = not edible, or edibility unknown
  1 = edible but worthless (tough, bitter, tiny)
  2 = edible, mediocre
  3 = good edible
  4 = very good edible
  5 = choice edible"""
TOXICITY_SCALE = """0 = no known toxicity
  1 = mildly toxic: may upset the stomach in some people, or only when raw/undercooked
  2 = poisonous: gastrointestinal poisoning
  3 = seriously poisonous: needs hospital care (e.g. muscarine, ibotenic acid, gyromitrin when cooked)
  4 = potentially deadly or organ-damaging
  5 = deadly even in small amounts (e.g. amatoxins, orellanine)"""
IDENTIFIABILITY_SCALE = """1 = needs microscopy or DNA
  2 = needs a spore print, smell, taste, staining or other tests the photo can't show
  3 = an experienced eye usually suffices
  4 = distinctive
  5 = unmistakable"""
SIMILAR_SPECIES_SCALE = """0 = no similar-looking species
  1 = one or two, easy to tell apart
  2 = a few, distinguishable with care
  3 = several, often confused
  4 = many, often confused
  5 = many near-identical species"""

PROMPT_TEMPLATE = """You are an expert mycologist identifying a mushroom or other fungus from a photo taken by a member of the public.

Location: {lat}, {lon}
{ecosystem_context}
{candidates_context}

Give:
- is_fungus: false if no mushroom or other fungus (cap-and-stem mushroom, bracket, puffball, cup, jelly, coral, etc.) is clearly visible in the photo (then fill the other fields with "unknown" / 0 / [])
- scientific_name: your best identification as a binomial (Genus species), the currently accepted name, without author names. If you can only get to genus, give just the genus.
- common_name: the usual English common name
- confidence: your honest probability (0 to 1) that scientific_name is right. Many fungi can only be separated by spore print, smell, flesh colour change or microscopy, none of which a photo shows - be conservative, and prefer a genus-level answer when species are hard to tell apart.
- alternatives: up to 3 other plausible species, each with scientific_name, common_name, confidence, edibility, toxicity, identifiability and similar_species (empty list if you are very sure)
- features: 1-2 sentences on the visible features that drove the identification (cap shape and colour, gills/pores/teeth, stem, ring or volva, surface, substrate)
- notes: 1-2 plain-English sentences about this fungus (habitat, what it grows with or on, season), relevant to this location.
- edibility: what the mycological literature says about eating the species in scientific_name, as an integer:
  {edibility_scale}
- toxicity: what the literature says about the toxicity of that same species, as an integer:
  {toxicity_scale}
  Rate edibility and toxicity for the named species, not from the photo. If you gave only a genus, rate the most toxic species of that genus that is plausible here. When the literature disagrees or is unknown, use the higher toxicity and the lower edibility.
- identifiability: how reliably the species in scientific_name can be identified from photos alone, in general (not this photo), as an integer:
  {identifiability_scale}
- similar_species: how many similar-looking species it has, and how close they are, as an integer:
  {similar_species_scale}
- safety_notes: 1 sentence on the toxins involved, or the conditions of edibility (e.g. only when well cooked, causes reactions with alcohol). Empty if nothing to add.
- dangerous_lookalikes: up to 3 poisonous species (toxicity 2 or more) that are commonly confused with this one, each with scientific_name, common_name and toxicity. Empty list if there are none.

Be concise. Use the location as evidence about what is likely, but identify what the photo actually shows."""


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _usage_by_key(db: Session) -> dict[int, int]:
    today = _today()
    rows = db.execute(select(AiUsage).where(AiUsage.usage_date == today)).scalars().all()
    return {r.key_index: r.count for r in rows}


# The Gemini key is shared with Naturalia and the other maps: every call is recorded in a shared
# ledger (shared_credits.py) under its group, and this app may use at most its own daily limit for
# its group ("fungus") - counting that group's calls made in Naturalia - and never more than what is
# left of the key's global limit. Without the ledger it falls back to counting its own calls.
APP, KIND = "shroommap", "fungus"


def _shared_remaining() -> int | None:
    if not shared_credits.enabled():
        return None
    try:
        return shared_credits.remaining(KIND, settings.gemini_daily_limit_per_key)
    except Exception:
        return None  # ledger unreachable: count locally


def credits_status(db: Session) -> dict:
    keys = settings.gemini_keys
    limit_per_key = settings.gemini_daily_limit_per_key
    usage = _usage_by_key(db)
    remaining = sum(max(0, limit_per_key - usage.get(i, 0)) for i in range(len(keys)))
    shared = _shared_remaining()
    if shared is not None:
        remaining = min(remaining, shared)
    return {
        "configured": len(keys) > 0,
        "remaining": remaining,
        "limit_total": limit_per_key * len(keys),
    }


def _increment_usage(db: Session, key_index: int) -> None:
    today = _today()
    stmt = (
        insert(AiUsage)
        .values(key_index=key_index, usage_date=today, count=1)
        .on_conflict_do_update(
            index_elements=["key_index", "usage_date"],
            set_={"count": AiUsage.count + 1},
        )
    )
    db.execute(stmt)
    db.commit()


def get_country(lat: float, lon: float) -> str:
    try:
        resp = httpx.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={
                "lat": lat,
                "lon": lon,
                "format": "jsonv2",
                "zoom": 3,
                "addressdetails": 1,
                "accept-language": "en",
            },
            headers={"User-Agent": "AlejandroShroomMap/1.0 (+https://github.com/)"},
            timeout=5.0,
        )
        resp.raise_for_status()
        return resp.json().get("address", {}).get("country") or "unknown"
    except Exception:
        return "unknown"


def _clamp01(x) -> float:
    try:
        return max(0.0, min(1.0, float(x)))
    except (TypeError, ValueError):
        return 0.0


def _score(x, unknown: int) -> int:
    """A 0-5 score from the model; `unknown` when it is missing or not a number."""
    try:
        return max(0, min(5, int(round(float(x)))))
    except (TypeError, ValueError):
        return unknown


def _safety(result: dict, alternatives: list[dict]) -> dict:
    """Edibility and toxicity scores for the identification.

    The toxicity shown is the WORST case over everything the photo could be - the AI's
    first choice, its alternatives and the known dangerous look-alikes - so a wrong
    identification can never turn a deadly mushroom into a harmless-looking one. A
    missing toxicity counts as 5 and a missing edibility as 0 (fail safe)."""
    lookalikes = []
    for la in result.get("dangerous_lookalikes") or []:
        name = taxonomy.clean_binomial(la.get("scientific_name"))
        if name:
            lookalikes.append({
                "scientific_name": name,
                "common_name": (la.get("common_name") or "").strip() or "unknown",
                "toxicity": _score(la.get("toxicity"), 5),
            })
    toxicity = _score(result.get("toxicity"), 5)
    worst = max([toxicity] + [a["toxicity"] for a in alternatives] + [la["toxicity"] for la in lookalikes])
    return {
        "edibility": _score(result.get("edibility"), 0),
        "toxicity": toxicity,
        "worst_toxicity": worst,
        "notes": (result.get("safety_notes") or "").strip(),
        "lookalikes": lookalikes[:3],
    }


def _reliability(confidence: float, others: list[float], identifiability, similar_species) -> dict:
    """How far to trust the identification, 0-1.

    share: this species' part of everything the AI considered (its confidence / the sum over it and
    the alternatives). The species factor then discounts species that are hard to identify from a
    photo (identifiability 1-5) and species with many close look-alikes (similar_species 0-5):
        score = share * (identifiability / 5) * (1 - similar_species / 10)
    Missing ratings count as the worst case (identifiability 1, similar_species 5)."""
    total = confidence + sum(others)
    share = confidence / total if total > 0 else 0.0
    ident = max(1, _score(identifiability, 1))
    similar = _score(similar_species, 5)
    return {
        "score": round(share * (ident / 5) * (1 - similar / 10), 3),
        "share": round(share, 3),
        "identifiability": ident,
        "similar_species": similar,
    }


def _reliability_all(result: dict, alternatives: list[dict], raw_alts: list[dict]) -> dict:
    """Reliability of the first choice, plus one per alternative (same order as `alternatives`) so
    the app can show the right one when someone switches to an alternative."""
    confs = [_clamp01(result.get("confidence"))] + [a["confidence"] for a in alternatives]
    out = _reliability(confs[0], confs[1:], result.get("identifiability"), result.get("similar_species"))
    out["alternatives"] = [
        _reliability(confs[i + 1], confs[:i + 1] + confs[i + 2:], raw.get("identifiability"), raw.get("similar_species"))
        for i, raw in enumerate(raw_alts)
    ]
    return out


def _ecosystem_text(eco: dict | None) -> str:
    if not eco:
        return "No ecoregion data is available at this exact location."
    return f"Ecosystem here: {eco['ecoregion']} ({eco['biome']}, {eco['realm']} realm)."


def _candidates_text(species: list[str]) -> str:
    if not species:
        return "No local fungus records were available for this location."
    return (
        "Fungi most often recorded and photographed by observers near this location "
        "(a prior only - it may be something not on this list): "
        + ", ".join(species)
        + "."
    )


def identify_leaf(db: Session, image_bytes: bytes, lat: float, lon: float) -> dict:
    keys = settings.gemini_keys
    if not keys:
        raise HTTPException(status_code=503, detail="AI identification is not configured")

    usage = _usage_by_key(db)
    limit = settings.gemini_daily_limit_per_key
    candidates = [i for i in range(len(keys)) if usage.get(i, 0) < limit]
    shared = _shared_remaining()
    if not candidates or shared == 0:
        raise HTTPException(status_code=429, detail="No AI credits left today")

    # The two location lookups are independent network calls - run them together.
    with ThreadPoolExecutor(max_workers=2) as pool:
        eco_future = pool.submit(ecology.ecosystem_for, lat, lon)
        local_future = pool.submit(taxonomy.local_species, lat, lon)
        ecosystem = eco_future.result()
        local = local_future.result()

    prompt = PROMPT_TEMPLATE.format(
        lat=lat,
        lon=lon,
        ecosystem_context=_ecosystem_text(ecosystem),
        candidates_context=_candidates_text(local),
        edibility_scale=EDIBILITY_SCALE,
        toxicity_scale=TOXICITY_SCALE,
        identifiability_scale=IDENTIFIABILITY_SCALE,
        similar_species_scale=SIMILAR_SPECIES_SCALE,
    )
    image_b64 = base64.b64encode(image_bytes).decode("ascii")

    body = {
        "contents": [
            {
                "parts": [
                    {"inline_data": {"mime_type": "image/jpeg", "data": image_b64}},
                    {"text": prompt},
                ]
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": RESPONSE_SCHEMA,
        },
    }

    last_error = None
    for i in candidates:
        try:
            resp = httpx.post(
                GEMINI_URL.format(model=settings.gemini_model),
                params={"key": keys[i]},
                json=body,
                timeout=30.0,
            )
            if resp.status_code == 429:
                last_error = "rate limited"
                continue
            resp.raise_for_status()
            data = resp.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            result = json.loads(text)
            _increment_usage(db, i)
            if shared is not None:
                try:
                    shared_credits.record(APP, KIND)
                except Exception:
                    pass  # the call succeeded; a missed ledger entry must not lose the answer
            return _shape_result(result, ecosystem, len(local))
        except HTTPException:
            raise
        except Exception as e:
            last_error = str(e)
            continue

    raise HTTPException(status_code=502, detail=f"AI identification failed: {last_error}")


def _shape_result(result: dict, ecosystem: dict | None, n_candidates: int) -> dict:
    """Turn the raw model answer into what the app stores/shows, checking the
    name against the GBIF backbone rather than trusting it as typed."""
    if not result.get("is_fungus"):
        return {
            "is_fungus": False,
            "title": "unknown",
            "scientific_name": "unknown",
            "description": "unknown",
            "features": "",
            "notes": "",
            "confidence": 0.0,
            "alternatives": [],
            "taxonomy": None,
            "ecosystem": ecosystem,
            "candidates_considered": n_candidates,
            "safety": None,
            "reliability": None,
        }

    asked = taxonomy.clean_binomial(result.get("scientific_name"))
    tax = taxonomy.resolve_taxon(asked)
    # Prefer the accepted species name when GBIF resolved it (turns a synonym
    # into the name the rest of the taxonomy - and the tree - uses).
    scientific = (tax or {}).get("species") or asked or "unknown"

    alternatives, raw_alts = [], []
    for alt in result.get("alternatives") or []:
        name = taxonomy.clean_binomial(alt.get("scientific_name"))
        if name and name != scientific:
            alternatives.append(
                {
                    "scientific_name": name,
                    "common_name": (alt.get("common_name") or "").strip() or "unknown",
                    "confidence": _clamp01(alt.get("confidence")),
                    "edibility": _score(alt.get("edibility"), 0),
                    "toxicity": _score(alt.get("toxicity"), 5),
                }
            )
            raw_alts.append(alt)

    features = (result.get("features") or "").strip()
    notes = (result.get("notes") or "").strip()
    return {
        "is_fungus": True,
        "title": (result.get("common_name") or "").strip() or "unknown",
        "scientific_name": scientific,
        "description": " ".join(p for p in (features, notes) if p) or "unknown",
        "features": features,
        "notes": notes,
        "confidence": _clamp01(result.get("confidence")),
        "alternatives": alternatives[:3],
        "safety": _safety(result, alternatives[:3]),
        "reliability": _reliability_all(result, alternatives[:3], raw_alts[:3]),
        "taxonomy": tax,
        "ecosystem": ecosystem,
        "candidates_considered": n_candidates,
    }
