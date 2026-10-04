import io
import json
import os
import uuid

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from geoalchemy2.shape import to_shape
from PIL import Image, ImageOps
from pillow_heif import register_heif_opener
from sqlalchemy import select
from sqlalchemy.orm import Session

import ai
import ecology
import taxonomy
import tree
from config import settings
from db import Base, engine, get_db
from models import Leaf

register_heif_opener()

app = FastAPI(title="Alejandro Shroom Map API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

os.makedirs(settings.uploads_dir, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=settings.uploads_dir), name="uploads")

# Cap per container start, so a long backlog (or a lookup service being down)
# can never make startup hang for minutes.
BACKFILL_LIMIT = 50


@app.on_event("startup")
def on_startup():
    with engine.connect() as conn:
        conn.exec_driver_sql("CREATE EXTENSION IF NOT EXISTS postgis")
        conn.commit()
    Base.metadata.create_all(bind=engine)
    # Base.metadata.create_all only creates missing tables, not missing
    # columns on tables that already exist - this app has no migration tool, so
    # a future new column gets an idempotent
    #   ALTER TABLE leaves ADD COLUMN IF NOT EXISTS ...
    # here, run on every container start.
    with engine.connect() as conn:
        # Rows written before models.py used none_as_null hold JSON null, which the
        # backfills below can't see; turn them into SQL NULL (no-op once clean).
        for col in ("taxonomy", "ecosystem", "ai_analysis"):
            conn.exec_driver_sql(f"UPDATE leaves SET {col} = NULL WHERE jsonb_typeof({col}) = 'null'")
        conn.commit()

    # Self-healing backfills: only touch rows still missing a value, so these are
    # no-ops once everything is filled in, and they repair rows whose lookup
    # failed last time (a GBIF / Nominatim / ecoregion-service hiccup).
    with Session(engine) as db:
        for leaf in db.execute(select(Leaf).where(Leaf.country == "unknown").limit(BACKFILL_LIMIT)).scalars():
            point = to_shape(leaf.location)
            leaf.country = ai.get_country(point.y, point.x)
        for leaf in db.execute(
            select(Leaf).where(Leaf.taxonomy.is_(None), Leaf.scientific_name != "unknown").limit(BACKFILL_LIMIT)
        ).scalars():
            leaf.taxonomy = taxonomy.resolve_taxon(leaf.scientific_name)
        for leaf in db.execute(select(Leaf).where(Leaf.ecosystem.is_(None)).limit(BACKFILL_LIMIT)).scalars():
            point = to_shape(leaf.location)
            leaf.ecosystem = ecology.ecosystem_for(point.y, point.x)
        db.commit()


@app.get("/api/health")
def health():
    return {"status": "ok"}


def _leaf_to_feature(leaf: Leaf) -> dict:
    point = to_shape(leaf.location)
    ai_info = leaf.ai_analysis or {}
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [point.x, point.y]},
        "properties": {
            "id": str(leaf.id),
            "title": leaf.title or "unknown",
            "scientific_name": leaf.scientific_name or "unknown",
            "description": leaf.description or "unknown",
            "taxonomy": leaf.taxonomy,
            "ecosystem": leaf.ecosystem,
            "country": leaf.country or "unknown",
            "added_by": leaf.added_by or "unknown",
            "photo_url": f"/uploads/{leaf.photo_filename}" if leaf.photo_filename else None,
            "created_at": leaf.created_at.isoformat() if leaf.created_at else None,
            "identification": (
                {
                    "confidence": ai_info.get("confidence"),
                    "alternatives": ai_info.get("alternatives") or [],
                    "features": ai_info.get("features"),
                    "safety": ai_info.get("safety"),
                    "reliability": ai_info.get("reliability"),
                }
                if ai_info
                else None
            ),
        },
    }


@app.get("/api/leaves")
def list_leaves(db: Session = Depends(get_db)):
    leaves = db.execute(select(Leaf).order_by(Leaf.created_at.desc())).scalars().all()
    return {"type": "FeatureCollection", "features": [_leaf_to_feature(x) for x in leaves]}


@app.get("/api/tree")
def species_tree(db: Session = Depends(get_db)):
    """Evolutionary tree of the species collected so far (see tree.py)."""
    taxa: dict[str, dict] = {}
    for leaf in db.execute(select(Leaf)).scalars():
        t = leaf.taxonomy or {}
        if t.get("species"):
            taxa.setdefault(t["species"], t)
    return tree.tree_for(taxa)


@app.get("/api/ai-credits")
def ai_credits(db: Session = Depends(get_db)):
    return ai.credits_status(db)


MAX_DIM = 1920
MAX_AI_DIM = 1024  # smaller copy sent to the AI: plenty for identification, cheaper/faster
ALLOWED_PHOTO_TYPES = ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif")


def _process_photo(raw: bytes, max_dim: int = MAX_DIM) -> bytes:
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)  # bake in rotation before stripping EXIF
    img = img.convert("RGB")
    img.thumbnail((max_dim, max_dim), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=85)  # no EXIF written -> strips GPS/metadata
    return out.getvalue()


def _read_photo(photo: UploadFile, lat: float, lon: float) -> bytes:
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="Invalid coordinates")
    if photo.content_type not in ALLOWED_PHOTO_TYPES:
        raise HTTPException(status_code=400, detail="Unsupported photo type")
    raw = photo.file.read()
    if len(raw) > settings.max_photo_mb * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Photo too large")
    return raw


# Plain `def` (not `async def`): these make slow blocking calls out to GBIF,
# Gemini etc., which FastAPI runs in its thread pool instead of stalling the
# event loop for every other visitor.
@app.post("/api/identify")
def identify_leaf(
    lat: float = Form(...),
    lon: float = Form(...),
    photo: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    raw = _read_photo(photo, lat, lon)
    try:
        small = _process_photo(raw, max_dim=MAX_AI_DIM)
    except Exception:
        raise HTTPException(status_code=400, detail="Could not read photo")

    result = ai.identify_leaf(db, small, lat, lon)
    result["credits"] = ai.credits_status(db)
    return result


@app.post("/api/leaves")
def create_leaf(
    lat: float = Form(...),
    lon: float = Form(...),
    title: str = Form(""),
    scientific_name: str = Form(""),
    description: str = Form(""),
    identification: str = Form(""),  # JSON {confidence, alternatives, features, safety, reliability}, optional
    passphrase: str = Form(...),
    photo: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    added_by = settings.passphrase_names.get(passphrase)
    if added_by is None:
        raise HTTPException(status_code=403, detail="Wrong passphrase")

    raw = _read_photo(photo, lat, lon)
    try:
        processed = _process_photo(raw)
    except Exception:
        raise HTTPException(status_code=400, detail="Could not read photo")

    filename = f"{uuid.uuid4()}.jpg"
    with open(os.path.join(settings.uploads_dir, filename), "wb") as f:
        f.write(processed)

    ai_analysis = None
    if identification.strip():
        try:
            parsed = json.loads(identification)
            if isinstance(parsed, dict):
                ai_analysis = parsed
        except (json.JSONDecodeError, TypeError):
            ai_analysis = None

    # Whatever name ends up here - AI-suggested or typed/corrected by a person -
    # is checked against the GBIF taxonomy, and stored as the accepted species
    # name when it resolves, so grouping and the tree always agree on the name.
    typed_name = scientific_name.strip()
    tax = taxonomy.resolve_taxon(typed_name)
    stored_name = (tax or {}).get("species") or typed_name or "unknown"

    leaf = Leaf(
        location=f"SRID=4326;POINT({lon} {lat})",
        title=title.strip() or "unknown",
        scientific_name=stored_name,
        description=description.strip() or "unknown",
        country=ai.get_country(lat, lon),
        added_by=added_by,
        photo_filename=filename,
        taxonomy=tax,
        ecosystem=ecology.ecosystem_for(lat, lon),
        ai_analysis=ai_analysis,
    )
    db.add(leaf)
    db.commit()
    db.refresh(leaf)
    return _leaf_to_feature(leaf)
