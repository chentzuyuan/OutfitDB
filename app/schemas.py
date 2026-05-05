from datetime import datetime
from typing import Optional, List, Any
from pydantic import BaseModel, Field, field_validator
from .models import (
    CategoryEnum, LayerRoleEnum, ThicknessEnum, ItemStateEnum,
    WeatherEnum, OccasionEnum, UserActionEnum, RatingSourceEnum,
)


# ─── Fabric composition ────────────────────────────────────────────────
class CompositionEntry(BaseModel):
    """One material in a fabric blend (e.g. {'material': 'cotton', 'pct': 40})."""
    material: str
    pct: int = Field(ge=1, le=100)

    @field_validator("material")
    @classmethod
    def _lowercase(cls, v: str) -> str:
        return v.lower().strip()


def _validate_composition(comp: Optional[List[CompositionEntry]]) -> Optional[List[CompositionEntry]]:
    """Sum-to-100 + no duplicate materials. Returns the list as-is on success."""
    if not comp:
        return None
    total = sum(c.pct for c in comp)
    if total != 100:
        raise ValueError(f"composition pct must sum to 100, got {total}")
    seen = set()
    for c in comp:
        if c.material in seen:
            raise ValueError(f"duplicate material '{c.material}' in composition")
        seen.add(c.material)
    return comp


class ItemBase(BaseModel):
    name: str
    category: CategoryEnum
    layer_role: LayerRoleEnum = LayerRoleEnum.none
    colors: List[str] = []
    is_multicolor: bool = False
    pattern: str = "solid"
    pattern_complexity: int = 0
    material: str = "cotton"
    # Optional fabric blend. None = item is 100% `material`. When set, must
    # sum to 100 and contain no duplicate materials.
    composition: Optional[List[CompositionEntry]] = None
    thickness: ThicknessEnum = ThicknessEnum.thin
    style_tags: List[str] = []
    can_wear_alone: bool = True
    shoulder_fit: Optional[str] = None
    waist_fit: Optional[str] = None
    collar: Optional[str] = None
    top_length: Optional[str] = None
    sleeve: Optional[str] = None
    pants_length: Optional[str] = None
    pants_fit: Optional[str] = None

    @field_validator("composition")
    @classmethod
    def _check_composition(cls, v):
        return _validate_composition(v)


class ItemCreate(ItemBase):
    pass


class ItemOut(ItemBase):
    id: int
    user_id: int
    image_path: Optional[str] = None
    is_active: bool
    created_at: datetime
    state: Optional[ItemStateEnum] = None
    coverage_count: int = 0
    worn_count: int = 0
    average_rating: Optional[float] = None
    # Laundry tracking
    wears_per_wash: int = 3
    wear_count_since_wash: int = 0

    class Config:
        from_attributes = True


class ItemStatePatch(BaseModel):
    state: ItemStateEnum


# ─── Laundry / cleanliness ─────────────────────────────────────────────
class ItemCleanlinessPatch(BaseModel):
    """Manually adjust an item's cleanliness — any of the three knobs."""
    wear_count_since_wash: Optional[int] = None
    state: Optional[ItemStateEnum] = None
    wears_per_wash: Optional[int] = None


class ItemBulkLaunderRequest(BaseModel):
    item_ids: List[int]


class ItemUpdate(BaseModel):
    """General-purpose item update (extendable; currently wears_per_wash + name + composition)."""
    wears_per_wash: Optional[int] = None
    name: Optional[str] = None
    composition: Optional[List[CompositionEntry]] = None
    material: Optional[str] = None  # primary material; usually auto-derived but settable

    @field_validator("composition")
    @classmethod
    def _check_composition(cls, v):
        return _validate_composition(v)


class DailyContextCreate(BaseModel):
    date: datetime
    temperature: Optional[float] = None
    temperature_high: Optional[float] = None
    temperature_low: Optional[float] = None
    weather: Optional[WeatherEnum] = None
    occasion: OccasionEnum = OccasionEnum.casual
    notes: Optional[str] = None


class DailyContextOut(DailyContextCreate):
    id: int
    user_id: int

    class Config:
        from_attributes = True


class OutfitItemOut(BaseModel):
    item_id: int
    position: int = 0
    item: Optional[ItemOut] = None

    class Config:
        from_attributes = True


class OutfitOut(BaseModel):
    id: int
    user_id: int
    context_id: Optional[int] = None
    warmth_score: float
    inner_count: int
    mid_count: int
    outer_count: int
    total_items: int
    is_generated: bool
    coverage_curve: Optional[List[float]] = None
    aesthetic_curve: Optional[List[float]] = None
    optimal_layer_count: Optional[int] = None
    overkill_layers: int = 0
    underfit_flag: bool = False
    aesthetic_stop_layers: Optional[List[int]] = None
    outfit_items: List[OutfitItemOut] = []

    class Config:
        from_attributes = True


class RatingCreate(BaseModel):
    outfit_id: int
    rating: int = Field(..., ge=-1, le=2)
    rating_source: RatingSourceEnum = RatingSourceEnum.user_rated


class RatingOut(BaseModel):
    id: int
    user_id: int
    outfit_id: int
    rating: int
    rating_source: RatingSourceEnum
    rated_at: datetime

    class Config:
        from_attributes = True


class GenerateRequest(BaseModel):
    context_id: int
    n: int = 100
    must_include_item_id: Optional[int] = None


class ManualOutfitCreate(BaseModel):
    context_id: int
    item_ids: List[int]


class RecommendRequest(BaseModel):
    context_id: int
    top_k: int = 5
    must_include_item_ids: Optional[List[int]] = None
    # User clicked "Try anyway" after a soft-block warning — relax the
    # legality check on the pinned set (e.g. allow 2 outer layers).
    bypass_pinned_legality: bool = False


class ScoredOutfit(BaseModel):
    outfit: OutfitOut
    total_score: float
    preference_score: float
    context_fit_score: float
    freshness_score: float
    diversity_score: float
    recovery_score: float
    explanation: str


class OutfitLogCreate(BaseModel):
    context_id: int
    recommended_outfit_id: Optional[int] = None
    final_worn_outfit_id: Optional[int] = None
    user_action: UserActionEnum = UserActionEnum.accepted


class TrainResult(BaseModel):
    status: str
    training_samples: int
    val_auc: Optional[float] = None
    val_logloss: Optional[float] = None
    model_path: Optional[str] = None
    message: Optional[str] = None
