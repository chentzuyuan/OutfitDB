import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text,
    ForeignKey, Enum, UniqueConstraint, CheckConstraint, JSON,
)
from sqlalchemy.orm import relationship
from .database import Base


class CategoryEnum(str, enum.Enum):
    top = "top"
    bottom = "bottom"
    shoes = "shoes"
    accessory = "accessory"
    fullbody = "fullbody"  # one-piece (dress / jumpsuit / Superman suit) — counts as both top + bottom


class LayerRoleEnum(str, enum.Enum):
    inner = "inner"
    mid = "mid"
    outer = "outer"
    none = "none"


class ThicknessEnum(str, enum.Enum):
    very_thin = "very_thin"
    thin = "thin"
    thick = "thick"
    very_thick = "very_thick"


THICKNESS_VALUES = {"very_thin": 1, "thin": 2, "thick": 3, "very_thick": 4}


class ItemStateEnum(str, enum.Enum):
    clean = "clean"
    worn = "worn"
    in_laundry = "in_laundry"
    unavailable = "unavailable"


class UserActionEnum(str, enum.Enum):
    accepted = "accepted"
    modified = "modified"
    rejected = "rejected"
    skipped = "skipped"


class RatingSourceEnum(str, enum.Enum):
    user_rated = "user_rated"
    user_modified = "user_modified"


class WeatherEnum(str, enum.Enum):
    sunny = "sunny"
    cloudy = "cloudy"
    rainy = "rainy"
    snowy = "snowy"
    windy = "windy"


class OccasionEnum(str, enum.Enum):
    casual = "casual"
    work = "work"
    formal = "formal"
    sport = "sport"
    home = "home"


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(64), unique=True, nullable=False)
    email = Column(String(128), unique=True, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    # location for client-side weather fetch (Phase 3)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    city = Column(String(64), nullable=True)
    timezone = Column(String(64), nullable=True)
    # onboarding training (calibration)
    training_complete = Column(Boolean, default=False, nullable=False)
    temp_offset = Column(Float, default=0.0, nullable=False)  # cold-sensitivity bias (legacy)
    # Display preference for temperature: 'C' (default) or 'F'. The DB always
    # stores Celsius — this only affects what the UI shows / accepts as input.
    temp_unit = Column(String(1), default="C", nullable=False)
    # ─── Phase 4: multi-stage training flags ─────────────────────────────
    v2_temp_done = Column(Boolean, default=False, nullable=False)
    v2_aesthetic_done = Column(Boolean, default=False, nullable=False)
    v2_occasion_done = Column(Boolean, default=False, nullable=False)
    # JSON dict capturing per-zone warmth preferences from stage 1 adaptive
    # sampling. Shape: {"cool": [min_warmth, max_warmth], ...}.
    zone_warmth_prefs = Column(Text, nullable=True)


class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(128), nullable=False)
    category = Column(Enum(CategoryEnum), nullable=False)
    layer_role = Column(Enum(LayerRoleEnum), nullable=False, default=LayerRoleEnum.none)
    colors = Column(JSON, nullable=False, default=list)
    is_multicolor = Column(Boolean, default=False, nullable=False)
    pattern = Column(String(32), nullable=False, default="solid")
    pattern_complexity = Column(Integer, default=0, nullable=False)
    material = Column(String(32), nullable=False, default="cotton")
    # Optional fabric blend. None = item is 100% `material`. When set, this is
    # a list like [{"material": "cotton", "pct": 40}, {"material": "linen", "pct": 60}]
    # with pct values summing to 100. The `material` column above mirrors the
    # entry with the highest pct (so legacy filters/queries still work).
    composition = Column(JSON, nullable=True)
    thickness = Column(Enum(ThicknessEnum), nullable=False, default=ThicknessEnum.thin)
    style_tags = Column(JSON, nullable=False, default=list)
    can_wear_alone = Column(Boolean, default=True, nullable=False)
    # top-specific (also used by fullbody)
    shoulder_fit = Column(String(16), nullable=True)
    waist_fit = Column(String(16), nullable=True)
    collar = Column(String(16), nullable=True)
    top_length = Column(String(16), nullable=True)
    sleeve = Column(String(16), nullable=True)  # long / short / sleeveless
    # bottom-specific
    pants_length = Column(String(16), nullable=True)
    pants_fit = Column(String(16), nullable=True)
    image_path = Column(String(256), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    # Laundry: how many wears before this item must go through laundry.
    # Smaller for socks/underwear/tees (1–2), larger for jeans/coats (5–15).
    # User-overridable per item; default seeded by category+material heuristics.
    wears_per_wash = Column(Integer, default=3, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    tags = relationship("ItemTag", back_populates="item", cascade="all, delete-orphan")
    state = relationship("ItemState", back_populates="item", uselist=False, cascade="all, delete-orphan")
    stats = relationship("ItemStats", back_populates="item", uselist=False, cascade="all, delete-orphan")


class ItemTag(Base):
    __tablename__ = "item_tags"
    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False, index=True)
    key = Column(String(64), nullable=False)
    value = Column(String(128), nullable=False)
    __table_args__ = (UniqueConstraint("item_id", "key", "value", name="uq_item_tag"),)
    item = relationship("Item", back_populates="tags")


class ItemState(Base):
    __tablename__ = "item_states"
    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False, unique=True)
    state = Column(Enum(ItemStateEnum), nullable=False, default=ItemStateEnum.clean)
    worn_count = Column(Integer, default=0, nullable=False)
    wear_count_since_wash = Column(Integer, default=0, nullable=False)
    last_worn = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    item = relationship("Item", back_populates="state")


class DailyContext(Base):
    __tablename__ = "daily_contexts"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    date = Column(DateTime, nullable=False)
    temperature = Column(Float, nullable=True)
    temperature_high = Column(Float, nullable=True)
    temperature_low = Column(Float, nullable=True)
    weather = Column(Enum(WeatherEnum), nullable=True)
    occasion = Column(Enum(OccasionEnum), nullable=False, default=OccasionEnum.casual)
    notes = Column(Text, nullable=True)
    calendar_event = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_user_date_context"),)


class Outfit(Base):
    __tablename__ = "outfits"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    context_id = Column(Integer, ForeignKey("daily_contexts.id"), nullable=True, index=True)
    warmth_score = Column(Float, nullable=False, default=0.0)
    inner_count = Column(Integer, default=0, nullable=False)
    mid_count = Column(Integer, default=0, nullable=False)
    outer_count = Column(Integer, default=0, nullable=False)
    total_items = Column(Integer, default=0, nullable=False)
    is_generated = Column(Boolean, default=True, nullable=False)
    coverage_curve = Column(JSON, nullable=True)
    aesthetic_curve = Column(JSON, nullable=True)
    optimal_layer_count = Column(Integer, nullable=True)
    overkill_layers = Column(Integer, default=0, nullable=False)
    underfit_flag = Column(Boolean, default=False, nullable=False)
    aesthetic_stop_layers = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    outfit_items = relationship("OutfitItem", back_populates="outfit", cascade="all, delete-orphan")


class OutfitItem(Base):
    __tablename__ = "outfit_items"
    id = Column(Integer, primary_key=True, index=True)
    outfit_id = Column(Integer, ForeignKey("outfits.id"), nullable=False, index=True)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False, index=True)
    position = Column(Integer, default=0)
    __table_args__ = (UniqueConstraint("outfit_id", "item_id", name="uq_outfit_item"),)

    outfit = relationship("Outfit", back_populates="outfit_items")
    item = relationship("Item")


class Rating(Base):
    __tablename__ = "ratings"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    outfit_id = Column(Integer, ForeignKey("outfits.id"), nullable=False, index=True)
    rating = Column(Integer, nullable=False)
    rating_source = Column(Enum(RatingSourceEnum), nullable=False, default=RatingSourceEnum.user_rated)
    # ideal temperature zone for this outfit (per the user) — feeds temp_offset calibration
    ideal_temp_zone = Column(String(8), nullable=True)  # 'cold' / 'cool' / 'mild' / 'warm' / 'hot'
    rated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (
        CheckConstraint("rating IN (-1, 0, 1, 2)", name="ck_rating_valid"),
        UniqueConstraint("user_id", "outfit_id", name="uq_user_outfit_rating"),
    )


# ─── Multi-stage training (Phase 4) ──────────────────────────────────────
# Three independent training tracks so user feedback isn't polluted by
# off-axis confounds. Stage 1 trains pure temperature appropriateness,
# stage 2 trains pure aesthetic preference, stage 3 trains occasion fit.

# Temperature zones (6) — finer-grained than the old 5-zone calibration.
# Used by stage 1 (TemperatureRating) and adaptive sampling.
TEMP_ZONE_KEYS = ["subzero", "cold", "cool", "mild", "warm", "hot"]
TEMP_ZONE_RANGES = {
    "subzero": "<0°C",
    "cold":    "0–10°C",
    "cool":    "10–18°C",
    "mild":    "18–25°C",
    "warm":    "25–30°C",
    "hot":     ">30°C",
}
TEMP_ZONE_REPRESENTATIVE = {
    "subzero": -5.0,
    "cold":     5.0,
    "cool":    14.0,
    "mild":    22.0,
    "warm":    27.0,
    "hot":     32.0,
}

# Event keys for stage 3 occasion training. Hard-coded list — order matters
# for UI display.
EVENT_KEYS = [
    "home", "gym", "beach", "casual_outing",
    "date_night", "interview",
    "office", "business_meeting", "formal_event",
]
# Fallback formality (only used pre-training; once stage3_model is trained
# it overrides these).
EVENT_FORMALITY = {
    "home": 0, "gym": 0, "beach": 1, "casual_outing": 2,
    "date_night": 3, "interview": 4,
    "office": 3, "business_meeting": 4, "formal_event": 5,
}


class TemperatureRating(Base):
    """Stage 1 — user marks which temp zones an outfit suits.
    `zones_ok` is a JSON list of zone keys (subset of TEMP_ZONE_KEYS).
    Empty list = "not appropriate for any zone" (negative signal).
    """
    __tablename__ = "temperature_ratings"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    outfit_id = Column(Integer, ForeignKey("outfits.id"), nullable=False, index=True)
    zones_ok = Column(JSON, nullable=False)  # list[str]
    target_zone = Column(String(8), nullable=True)  # the zone we sampled this outfit for (analytics)
    rated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("user_id", "outfit_id", name="uq_user_outfit_temp_rating"),
    )


class OccasionRating(Base):
    """Stage 3 — user marks which events an outfit suits."""
    __tablename__ = "occasion_ratings"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    outfit_id = Column(Integer, ForeignKey("outfits.id"), nullable=False, index=True)
    events_ok = Column(JSON, nullable=False)  # list[str]
    rated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    __table_args__ = (
        UniqueConstraint("user_id", "outfit_id", name="uq_user_outfit_occ_rating"),
    )


class OutfitLog(Base):
    __tablename__ = "outfit_logs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    context_id = Column(Integer, ForeignKey("daily_contexts.id"), nullable=False)
    recommended_outfit_id = Column(Integer, ForeignKey("outfits.id"), nullable=True)
    final_worn_outfit_id = Column(Integer, ForeignKey("outfits.id"), nullable=True)
    user_action = Column(Enum(UserActionEnum), nullable=False, default=UserActionEnum.skipped)
    scheduled_for = Column(DateTime, nullable=True)
    is_scheduled = Column(Boolean, default=False, nullable=False)
    logged_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ItemStats(Base):
    __tablename__ = "item_stats"
    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("items.id"), nullable=False, unique=True)
    coverage_count = Column(Integer, default=0, nullable=False)
    total_ratings = Column(Integer, default=0, nullable=False)
    average_rating = Column(Float, nullable=True)
    last_worn_date = Column(DateTime, nullable=True)
    days_since_worn = Column(Integer, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    item = relationship("Item", back_populates="stats")


class ModelRun(Base):
    __tablename__ = "model_runs"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    model_path = Column(String(256), nullable=False)
    training_samples = Column(Integer, nullable=False)
    feature_version = Column(String(32), nullable=False, default="v1")
    val_auc = Column(Float, nullable=True)
    val_logloss = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)
    trained_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
