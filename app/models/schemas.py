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


class SuggestedMatch(BaseModel):
    candidate_supplier_hotel_id: int
    candidate_supplier_name: str

    ai_similarity_score: float
    ai_decision: str


class SuggestedMatchesResponse(BaseModel):
    matches: list[SuggestedMatch]
    
    
# ─── Manual Review Schemas ───

class ManualReviewItem(BaseModel):
    # Internal supplier_hotels.id — used by frontend/API
    supplier_hotel_id: int

    # Actual supplier-provided hotel ID
    supplier_hotel_code: Optional[str] = None

    supplier_name: str
    hotel_name: Optional[str] = None
    normalized_name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: Optional[str] = None
    postal_code: Optional[str] = None
    star_rating: Optional[float] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    suggested_master_hotel_id: int

    master_hotel_name: Optional[str] = None
    master_normalized_name: Optional[str] = None
    master_address: Optional[str] = None
    master_city: Optional[str] = None
    master_state: Optional[str] = None
    master_country: Optional[str] = None
    master_postal_code: Optional[str] = None
    master_star_rating: Optional[float] = None
    master_latitude: Optional[float] = None
    master_longitude: Optional[float] = None

    rule_score: float
    ai_similarity: Optional[float] = None
    decision_reason: Optional[str] = None
    created_at: datetime

class ManualReviewResponse(BaseModel):
    reviews: list[ManualReviewItem]
    
    
class ManualReviewDetail(BaseModel):
    supplier_hotel_id: int

    supplier_name: str
    supplier_hotel_code: Optional[str] = None

    hotel_name: Optional[str]
    normalized_name: Optional[str]
    address: Optional[str]
    city: Optional[str]
    state: Optional[str]
    country: Optional[str]
    postal_code: Optional[str]
    star_rating: Optional[float]
    latitude: Optional[float]
    longitude: Optional[float]

    suggested_master_hotel_id: int

    master_hotel_name: Optional[str]
    master_normalized_name: Optional[str]
    master_address: Optional[str]
    master_city: Optional[str]
    master_state: Optional[str]
    master_country: Optional[str]
    master_postal_code: Optional[str]
    master_star_rating: Optional[float]
    master_latitude: Optional[float]
    master_longitude: Optional[float]

    rule_score: float
    ai_similarity: Optional[float]
    decision_reason: Optional[str]

    created_at: datetime