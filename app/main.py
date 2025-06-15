import os
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from .database import Base, engine, SessionLocal
from .models import User, ChargingSession
from .auth import hash_password, verify_password

load_dotenv()

Base.metadata.create_all(bind=engine)

app = FastAPI(title="EV Charging Service")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def authenticate_user(db: Session, username: str, password: str):
    user = db.query(User).filter(User.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        return None
    return user


@app.post("/signup")
def signup(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == form.username).first():
        raise HTTPException(status_code=400, detail="Username already registered")
    user = User(username=form.username, password_hash=hash_password(form.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "username": user.username}


@app.post("/token")
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(db, form.username, form.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return {"access_token": user.username, "token_type": "bearer"}


@app.post("/topup")
def topup(amount: float, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == token).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.balance += amount
    db.commit()
    return {"balance": user.balance}


@app.post("/session/start")
def start_session(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == token).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.balance <= 0:
        raise HTTPException(status_code=400, detail="Insufficient balance")
    session = ChargingSession(user_id=user.id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return {"session_id": session.id}


@app.post("/session/stop")
def stop_session(session_id: int, energy: float, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == token).first()
    session = db.query(ChargingSession).filter(ChargingSession.id == session_id, ChargingSession.active == True).first()
    if not session or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    cost_per_kwh = float(os.getenv("PRICE_PER_KWH", "0.2"))
    cost = energy * cost_per_kwh
    if user.balance < cost:
        session.active = False
        db.commit()
        raise HTTPException(status_code=400, detail="Insufficient balance for consumed energy")
    user.balance -= cost
    session.energy = energy
    session.active = False
    db.commit()
    return {"remaining_balance": user.balance}
