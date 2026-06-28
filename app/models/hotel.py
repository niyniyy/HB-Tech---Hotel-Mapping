from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime,
    Integer, Numeric, String, Text, func
)
from sqlalchemy.dialects.postgresql import JSONB
from app.database.connection import Base
from pgvector.sqlalchemy import Vector


class SupplierHotel(Base):
    """
    Table 1: supplier_hotels
    Stores raw supplier hotel data exactly as received.
    Never modify data in this table after import.
    """
    __tablename__ = "supplier_hotels"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    supplier_name = Column(String(50), nullable=False, index=True)
    supplier_hotel_id = Column(String(100), nullable=False)

    hotel_name = Column(Text, nullable=True)
    normalized_name = Column(Text, nullable=True)

    address = Column(Text, nullable=True)
    city = Column(String(100), nullable=True, index=True)
    state = Column(String(100), nullable=True)
    country = Column(String(100), nullable=True, index=True)
    postal_code = Column(String(20), nullable=True)

    latitude = Column(Numeric(10, 7), nullable=True)
    longitude = Column(Numeric(10, 7), nullable=True)
    # geo_location is a PostGIS geography column — created via raw SQL in migration
    # geo_location = geography(Point, 4326)

    star_rating = Column(Numeric(2, 1), nullable=True)
    chain_name = Column(String(100), nullable=True)

    raw_json = Column(JSONB, nullable=True)

    created_at = Column(DateTime, default=func.now(), nullable=False)

    def __repr__(self):
        return f"<SupplierHotel id={self.id} supplier={self.supplier_name} name={self.hotel_name}>"


class MasterHotel(Base):
    """
    Table 2: master_hotels
    Single deduplicated hotel repository.
    One record per physical hotel regardless of supplier.
    """
    __tablename__ = "master_hotels"

    master_hotel_id = Column(BigInteger, primary_key=True, autoincrement=True)

    hotel_name = Column(Text, nullable=True)
    normalized_name = Column(Text, nullable=True)

    address = Column(Text, nullable=True)
    city = Column(String(100), nullable=True, index=True)
    state = Column(String(100), nullable=True)
    country = Column(String(100), nullable=True, index=True)
    postal_code = Column(String(20), nullable=True)

    latitude = Column(Numeric(10, 7), nullable=True)
    longitude = Column(Numeric(10, 7), nullable=True)
    # geo_location is a PostGIS geography column — created via raw SQL in migration

    star_rating = Column(Numeric(2, 1), nullable=True)

    created_at = Column(DateTime, default=func.now(), nullable=False)

    def __repr__(self):
        return f"<MasterHotel id={self.master_hotel_id} name={self.hotel_name}>"


class HotelMapping(Base):
    """
    Table 3: hotel_mappings
    Maps supplier hotels to master hotels.
    One record per supplier hotel → master hotel relationship.
    """
    __tablename__ = "hotel_mappings"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    master_hotel_id = Column(BigInteger, nullable=False, index=True)

    supplier_name = Column(String(50), nullable=False)
    supplier_hotel_id = Column(String(100), nullable=False)

    match_score = Column(Numeric(5, 2), nullable=True)

    # AUTO = auto-matched by engine, MANUAL = verified by human
    mapping_type = Column(String(50), nullable=True)
    is_manual_verified = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=func.now(), nullable=False)

    def __repr__(self):
        return f"<HotelMapping id={self.id} supplier={self.supplier_name} score={self.match_score}>"


class HotelMappingQueue(Base):
    """
    Table 4: hotel_mapping_queue
    Queue for background processing of hotel matching.
    Celery worker reads from this table.
    """
    __tablename__ = "hotel_mapping_queue"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    supplier_hotel_id = Column(BigInteger, nullable=False, index=True)

    # Pending → Processing → Completed / Failed / ManualReview
    status = Column(String(20), default="Pending", nullable=False, index=True)
    retry_count = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime, default=func.now(), nullable=False)

    def __repr__(self):
        return f"<HotelMappingQueue id={self.id} status={self.status}>"


class HotelEmbedding(Base):
    """
    Table 5: hotel_embeddings
    Stores vector embeddings for AI-based similarity matching.
    Uses pgvector extension — vector column created via raw SQL.
    """
    __tablename__ = "hotel_embeddings"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    master_hotel_id = Column(BigInteger, nullable=True, index=True)
    supplier_hotel_id = Column(BigInteger, nullable=True, index=True)
    supplier_name = Column(String(50), nullable=True)

    embedding = Column(Vector(384), nullable=False)

    created_at = Column(DateTime, default=func.now(), nullable=False)

    def __repr__(self):
        return f"<HotelEmbedding id={self.id} supplier={self.supplier_name}>"


class ManualReviewCandidate(Base):
    """
    Table 6: manual_review_candidates

    Stores the suggested match for hotels that require
    human verification before mapping.
    """

    __tablename__ = "manual_review_candidates"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    supplier_hotel_id = Column(
        BigInteger,
        nullable=False,
        index=True
    )

    suggested_master_hotel_id = Column(
        BigInteger,
        nullable=False,
        index=True
    )

    rule_score = Column(
        Numeric(5, 2),
        nullable=False
    )

    ai_similarity = Column(
        Numeric(5, 4),
        nullable=True
    )

    decision_reason = Column(
        Text,
        nullable=True
    )

    created_at = Column(
        DateTime,
        default=func.now(),
        nullable=False
    )

    def __repr__(self):
        return (
            f"<ManualReviewCandidate "
            f"supplier={self.supplier_hotel_id} "
            f"master={self.suggested_master_hotel_id}>"
        )