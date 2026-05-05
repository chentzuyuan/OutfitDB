from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import crud, schemas
from ..database import get_db
from ..services.model_training import train_model


router = APIRouter(prefix="/train", tags=["train"])


@router.post("/", response_model=schemas.TrainResult)
def train(db: Session = Depends(get_db)):
    user = crud.get_or_create_default_user(db)
    result = train_model(db, user.id)
    return result
