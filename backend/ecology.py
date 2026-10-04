"""Which ecosystem is this GPS point in?

Uses the RESOLVE "Ecoregions 2017" map (Dinerstein et al. 2017, BioScience; CC BY 4.0):
846 terrestrial ecoregions grouped into 14 biomes and 8 biogeographic realms,
served as a public ArcGIS feature layer, queried by point. Best-effort like the
other lookups: None when the point is off-land or the service is unreachable.
"""
import httpx

ECOREGIONS_URL = (
    "https://services.arcgis.com/P3ePLMYs2RVChkJx/arcgis/rest/services/"
    "Resolve_Ecoregions/FeatureServer/0/query"
)

_cache: dict[tuple, dict | None] = {}


def _query(lat: float, lon: float, search_radius_m: int | None) -> list:
    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "ECO_NAME,BIOME_NAME,REALM",
        "returnGeometry": "false",
        "f": "json",
    }
    if search_radius_m:
        params.update({"distance": search_radius_m, "units": "esriSRUnit_Meter"})
    resp = httpx.get(ECOREGIONS_URL, params=params, timeout=6.0)
    resp.raise_for_status()
    return resp.json().get("features") or []


def ecosystem_for(lat: float, lon: float) -> dict | None:
    key = (round(lat, 2), round(lon, 2))
    if key in _cache:
        return _cache[key]
    try:
        features = _query(lat, lon, None)
        # The polygons are coastline-generalised, so a GPS fix on a harbour, a
        # beach or a riverbank can land in a gap even though land is right there.
        # Widen the search in steps (the service doesn't sort by distance, so the
        # smallest radius that finds anything is the closest we can get) up to 10 km.
        for radius_m in (1_000, 3_000, 10_000):
            if features:
                break
            features = _query(lat, lon, radius_m)
    except Exception:
        return None  # transient - don't cache

    if not features:
        _cache[key] = None  # genuinely not near land - safe to remember
        return None
    a = features[0]["attributes"]
    result = {"ecoregion": a.get("ECO_NAME"), "biome": a.get("BIOME_NAME"), "realm": a.get("REALM")}
    if not result["ecoregion"] or result["ecoregion"] == "Rock and Ice":
        # Antarctica/Greenland ice sheet etc. - not a useful ecosystem for fungi
        _cache[key] = None
        return None
    _cache[key] = result
    return result
