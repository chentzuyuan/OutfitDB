from typing import List, Optional
from datetime import datetime, date as date_cls
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import asc, desc

from .. import crud, models, schemas
from ..database import get_db


router = APIRouter(prefix="/contexts", tags=["contexts"])


@router.post("/", response_model=schemas.DailyContextOut)
def upsert_context(payload: schemas.DailyContextCreate, db: Session = Depends(get_db)):
    user = crud.get_or_create_default_user(db)
    data = payload.model_dump()
    # normalize the date to midnight (so uniqueness works per calendar day)
    d = data["date"]
    if isinstance(d, datetime):
        data["date"] = datetime(d.year, d.month, d.day)
    ctx = crud.upsert_context(db, user.id, data)
    return ctx


@router.get("/", response_model=List[schemas.DailyContextOut])
def list_contexts(db: Session = Depends(get_db)):
    user = crud.get_or_create_default_user(db)
    return (
        db.query(models.DailyContext)
        .filter(models.DailyContext.user_id == user.id)
        .order_by(desc(models.DailyContext.date))
        .all()
    )


@router.get("/latest", response_model=Optional[schemas.DailyContextOut])
def latest_context(db: Session = Depends(get_db)):
    user = crud.get_or_create_default_user(db)
    return (
        db.query(models.DailyContext)
        .filter(models.DailyContext.user_id == user.id)
        .order_by(desc(models.DailyContext.date))
        .first()
    )
