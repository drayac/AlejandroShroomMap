"""Taxonomy and "what grows around here", both from GBIF (api.gbif.org, free, no key).

- resolve_taxon(): the authoritative Linnaean ranks for a scientific name, from the
  GBIF backbone taxonomy - so a name the AI (or a person) typed is checked against a
  real taxonomy instead of being trusted as-is.
- local_species(): the fungi people most often record around a GPS point. Used as a
  *prior* in the identification prompt (the point of "GPS as input"), never as a
  constraint on the answer.

Every call is best-effort: a GBIF hiccup must never break adding a mushroom, so failures
return None / [] and are not cached.
"""
import math
import re

import httpx

GBIF = "https://api.gbif.org/v1"
HEADERS = {"User-Agent": "AlejandroShroomMap/1.0 (+https://github.com/)"}
RANKS = ("kingdom", "phylum", "class", "order", "family", "genus", "species")
TAXON_KEY = 5  # GBIF backbone key of the kingdom Fungi

_taxon_cache: dict[str, dict] = {}
_local_cache: dict[tuple, list[str]] = {}


def clean_binomial(name: str | None) -> str | None:
    """'Alliaria petiolata (M.Bieb.) Cavara & Grande' -> 'Alliaria petiolata'.

    Keeps just genus + epithet (or the genus alone), dropping authorship, so the
    same plant compares equal however a source spells it. None for empty/unknown.
    """
    if not name:
        return None
    m = re.match(r"\s*([A-Z][a-zA-Z-]+)(?:\s+(?:×\s*)?([a-z][a-z-]+))?", name.strip())
    if not m:
        return None
    genus, epithet = m.group(1), m.group(2)
    if genus.lower() == "unknown":
        return None
    return f"{genus} {epithet}" if epithet else genus


def resolve_taxon(name: str | None) -> dict | None:
    """Linnaean ranks + GBIF key for a scientific name, or None if it can't be placed."""
    binomial = clean_binomial(name)
    if not binomial:
        return None
    if binomial in _taxon_cache:
        return _taxon_cache[binomial]
    try:
        resp = httpx.get(
            f"{GBIF}/species/match",
            params={"name": binomial, "kingdom": "Fungi"},
            headers=HEADERS,
            timeout=6.0,
        )
        resp.raise_for_status()
        d = resp.json()
    except Exception:
        return None  # transient - don't cache

    # A made-up name often "matches" only the kingdom (matchType HIGHERRANK with
    # every other rank empty) - that places nothing, so it counts as unresolved.
    # A genus-level match ("Acer") is a legitimate, if coarser, identification.
    if d.get("matchType") == "NONE" or not d.get("genus"):
        return None
    result = {r: d.get(r) for r in RANKS}
    # For a synonym GBIF reports the ranks (and `species`) of the ACCEPTED taxon,
    # which is what we want to group and place on a tree by.
    result.update(
        gbif_key=d.get("speciesKey") or d.get("usageKey"),
        rank=(d.get("rank") or "").lower() or None,
        status=(d.get("status") or "").lower() or None,
        match_type=(d.get("matchType") or "").lower() or None,
    )
    _taxon_cache[binomial] = result
    return result


def local_species(lat: float, lon: float, limit: int = 40) -> list[str]:
    """Binomials of the fungi most often photographed/recorded near this point.

    Human observations with a photo (largely iNaturalist research-grade records
    that GBIF republishes) within ~30 km: i.e. the species people actually
    encounter and photograph there. Most-recorded first.
    """
    key = (round(lat, 1), round(lon, 1), limit)
    if key in _local_cache:
        return _local_cache[key]
    dlat = 0.3
    dlon = min(0.3 / max(math.cos(math.radians(lat)), 0.05), 20.0)
    try:
        resp = httpx.get(
            f"{GBIF}/occurrence/search",
            params={
                "taxonKey": TAXON_KEY,
                "decimalLatitude": f"{lat - dlat},{lat + dlat}",
                "decimalLongitude": f"{lon - dlon},{lon + dlon}",
                "basisOfRecord": "HUMAN_OBSERVATION",
                "mediaType": "StillImage",
                "limit": 0,
                "facet": "scientificName",
                "facetLimit": limit * 2,  # some entries collapse to the same binomial / are genus-only
            },
            headers=HEADERS,
            timeout=8.0,
        )
        resp.raise_for_status()
        facets = resp.json().get("facets") or []
    except Exception:
        return []  # transient - don't cache

    names: list[str] = []
    for c in (facets[0]["counts"] if facets else []):
        b = clean_binomial(c.get("name"))
        # species only (two words), no duplicates
        if b and " " in b and b not in names:
            names.append(b)
        if len(names) >= limit:
            break
    _local_cache[key] = names
    return names
