from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.db import Wallet, FlightSheet
from app.schemas import WalletCreate, WalletOut

router = APIRouter(prefix="/api/wallets", tags=["wallets"])


@router.post("", response_model=WalletOut)
def create_wallet(payload: WalletCreate, db: Session = Depends(get_db)):
    if db.query(Wallet).filter(Wallet.name == payload.name).first():
        raise HTTPException(400, f"Wallet '{payload.name}' already exists")
    wallet = Wallet(**payload.model_dump())
    db.add(wallet)
    db.commit()
    db.refresh(wallet)
    return wallet


@router.get("", response_model=list[WalletOut])
def list_wallets(coin: str | None = None, db: Session = Depends(get_db)):
    q = db.query(Wallet)
    if coin:
        q = q.filter(Wallet.coin == coin)
    return q.all()


@router.put("/{wallet_id}", response_model=WalletOut)
def update_wallet(wallet_id: int, payload: WalletCreate, db: Session = Depends(get_db)):
    wallet = db.get(Wallet, wallet_id)
    if not wallet:
        raise HTTPException(404, "Wallet not found")
    for k, v in payload.model_dump().items():
        setattr(wallet, k, v)
    db.commit()
    db.refresh(wallet)
    return wallet


@router.delete("/{wallet_id}")
def delete_wallet(wallet_id: int, db: Session = Depends(get_db)):
    wallet = db.get(Wallet, wallet_id)
    if not wallet:
        raise HTTPException(404, "Wallet not found")
    in_use = db.query(FlightSheet).filter(FlightSheet.wallet_id == wallet_id).count()
    if in_use:
        raise HTTPException(
            400,
            f"Cannot delete - {in_use} flight sheet(s) still reference this wallet. "
            f"Repoint or delete those first."
        )
    db.delete(wallet)
    db.commit()
    return {"status": "ok"}
