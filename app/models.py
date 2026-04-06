from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class ItemStatus(str, Enum):
    matched = "matched"
    llm_matched = "llm_matched"
    cached = "cached"
    no_match = "no_match"
    error = "error"


class SyncItemResult(BaseModel):
    name: str
    status: ItemStatus
    picnic_product_name: str | None = None
    picnic_product_id: str | None = None
    food_id: str | None = None
    score: float | None = None
    error: str | None = None


class SyncResult(BaseModel):
    timestamp: datetime
    total_items: int
    added_to_cart: int
    no_match: int
    errors: int
    items: list[SyncItemResult]


last_sync_result: SyncResult | None = None


class RecipePhotoStatus(BaseModel):
    id: str
    slug: str
    name: str
    description: str
    has_photo: bool
    image_url: str | None = None


class PhotoCandidate(BaseModel):
    source: str  # "dalle" or "brave"
    url: str
    title: str | None = None


class PhotoSearchResult(BaseModel):
    slug: str
    dalle_result: PhotoCandidate | None = None
    brave_results: list[PhotoCandidate] = []
    dalle_error: str | None = None
    brave_error: str | None = None
