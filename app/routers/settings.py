from typing import Optional, Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from .. import crud, models
from ..database import get_db


router = APIRouter(prefix="/users", tags=["settings"])


class UserSettingsOut(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None
    city: Optional[str] = None
    timezone: Optional[str] = None
    # Display unit preference. DB always stores Celsius; this just changes
    # the unit shown to the user and accepted as input across the UI.
    temp_unit: Literal["C", "F"] = "C"
    # Personal cold-tolerance flag — surfaces the user's "I always have
    # thermal insoles / inner layers" signal so the warmth model can
    # assume more invisible insulation, and the recommendation card can
    # stop suggesting the user add thermals.
    has_thermal_insoles: bool = False


class UserSettingsUpdate(BaseModel):
    lat: Optional[float] = None
    lon: Optional[float] = None
    city: Optional[str] = None
    timezone: Optional[str] = None
    temp_unit: Optional[Literal["C", "F"]] = None
    has_thermal_insoles: Optional[bool] = None


def _to_out(user: models.User) -> "UserSettingsOut":
    return UserSettingsOut(
        lat=user.lat, lon=user.lon, city=user.city, timezone=user.timezone,
        temp_unit=(user.temp_unit or "C"),
        has_thermal_insoles=bool(user.has_thermal_insoles),
    )


@router.get("/settings", response_model=UserSettingsOut)
def get_settings(db: Session = Depends(get_db)):
    user = crud.get_or_create_default_user(db)
    return _to_out(user)


@router.put("/settings", response_model=UserSettingsOut)
def put_settings(payload: UserSettingsUpdate, db: Session = Depends(get_db)):
    user = crud.get_or_create_default_user(db)
    if payload.lat is not None:
        user.lat = payload.lat
    if payload.lon is not None:
        user.lon = payload.lon
    if payload.city is not None:
        user.city = payload.city
    if payload.timezone is not None:
        user.timezone = payload.timezone
    if payload.temp_unit is not None:
        user.temp_unit = payload.temp_unit
    if payload.has_thermal_insoles is not None:
        user.has_thermal_insoles = payload.has_thermal_insoles
    db.commit()
    db.refresh(user)
    return _to_out(user)
