from typing import Optional
from pydantic import BaseModel, Field
from datetime import datetime


# ─── Supplier Hotel Schemas ───

class SupplierHotelBase(BaseModel):
    supplier_name: str
    supplier_hotel_id: str
    hotel_name: Optional[str] = None
    normalized_name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    star_rating: Optional[float] = None
    chain_name: Optional[str] = None


class SupplierHotelCreate(SupplierHotelBase):
    pass


class SupplierHotelResponse(SupplierHotelBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


# ─── Import Response ───

class ImportSummary(BaseModel):
    supplier_name: str
    total_rows: int
    inserted: int
    skipped: int
    errors: int
    message: str


# ─── Mapping Status Response ───

class MappingStatusResponse(BaseModel):
    total: int
    pending: int
    processing: int
    completed: int
    failed: int
    manual_review: int


# ─── Master Hotel Schemas ───

class MasterHotelBase(BaseModel):
    hotel_name: Optional[str] = None
    normalized_name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    star_rating: Optional[float] = None


class MasterHotelResponse(MasterHotelBase):
    master_hotel_id: int
    created_at: datetime

    class Config:
        from_attributes = True


# ─── Match Suggestion Schema ───

class MatchSuggestion(BaseModel):
    master_hotel_id: int
    hotel_name: Optional[str]
    address: Optional[str]
    city: Optional[str]
    country: Optional[str]
    star_rating: Optional[float]
    distance_meters: Optional[float]
    match_score: float
    geo_score: float
    name_score: float
    address_score: float
    star_score: float
    chain_score: float


class SuggestedMatchesResponse(BaseModel):
    supplier_hotel_id: str
    supplier_name: str
    hotel_name: Optional[str]
    candidates: list[MatchSuggestion]
