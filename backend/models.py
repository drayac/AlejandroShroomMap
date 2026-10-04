import uuid

from geoalchemy2 import Geometry
from sqlalchemy import Column, Date, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from db import Base


# JSONB(none_as_null=True): a Python None must be stored as SQL NULL, not as the JSON value
# null - otherwise the startup backfills (`... IS NULL`) never retry a failed lookup.
class Leaf(Base):
    __tablename__ = "leaves"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    location = Column(Geometry(geometry_type="POINT", srid=4326), nullable=False)
    # The everyday name (usually the English common name); editable.
    title = Column(String, nullable=False, default="unknown")
    # The binomial species name as entered/confirmed - "unknown" if not identified.
    scientific_name = Column(String, nullable=False, default="unknown")
    description = Column(String, nullable=False, default="unknown")
    country = Column(String, nullable=False, default="unknown")
    added_by = Column(String, nullable=False, default="unknown")
    photo_filename = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # Linnaean ranks resolved against the GBIF backbone for scientific_name:
    # {kingdom, phylum, class, order, family, genus, species, gbif_key, rank,
    #  status, match_type}. Null when the name could not be resolved.
    taxonomy = Column(JSONB(none_as_null=True), nullable=True)
    # Where it was found, from the RESOLVE/WWF ecoregion map:
    # {ecoregion, biome, realm}. Null when the point is not on land / lookup failed.
    ecosystem = Column(JSONB(none_as_null=True), nullable=True)
    # What the AI said about it when the leaf was filled in via automatic
    # identification: {confidence, alternatives, features}. Null for manual entries.
    ai_analysis = Column(JSONB(none_as_null=True), nullable=True)


class AiUsage(Base):
    __tablename__ = "ai_usage"

    key_index = Column(Integer, primary_key=True)
    usage_date = Column(Date, primary_key=True)
    count = Column(Integer, nullable=False, default=0)
