import os
from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    status,
    Request,
    Form,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from .database import Base, engine, SessionLocal
from .models import User, ChargingSession
from .auth import hash_password, verify_password

load_dotenv()

Base.metadata.create_all(bind=engine)

app = FastAPI(title="EV Charging Service")
templates = Jinja2Templates(directory="app/templates")

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


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/signup", response_class=HTMLResponse)
def signup_form(request: Request):
    return templates.TemplateResponse("signup.html", {"request": request})


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


def get_current_user(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user = db.query(User).filter(User.username == token).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse("dashboard.html", {"request": request, "user": user})


@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden")
    users = db.query(User).all()
    return templates.TemplateResponse("admin.html", {"request": request, "users": users})


@app.get("/logout")
def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("token")
    return response


@app.post("/signup")
def signup(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=400, detail="Username already registered")
    user = User(username=username, password_hash=hash_password(password))
    db.add(user)
    db.commit()
    db.refresh(user)
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie("token", user.username)
    return response


@app.post("/api/signup")
def signup_api(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    if db.query(User).filter(User.username == form.username).first():
        raise HTTPException(status_code=400, detail="Username already registered")
    user = User(username=form.username, password_hash=hash_password(form.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": user.id, "username": user.username}


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    user = authenticate_user(db, username, password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie("token", user.username)
    return response


@app.post("/token")
def login_api(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(db, form.username, form.password)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return {"access_token": user.username, "token_type": "bearer"}


@app.post("/topup")
def topup(request: Request, amount: float = Form(...), db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    user.balance += amount
    db.commit()
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/api/topup")
def topup_api(amount: float, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == token).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    user.balance += amount
    db.commit()
    return {"balance": user.balance}


@app.post("/session/start")
def start_session(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if user.balance <= 0:
        raise HTTPException(status_code=400, detail="Insufficient balance")
    session = ChargingSession(user_id=user.id)
    db.add(session)
    db.commit()
    db.refresh(session)
    return RedirectResponse(f"/dashboard", status_code=303)


@app.post("/api/session/start")
def start_session_api(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
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
def stop_session(session_id: int = Form(...), energy: float = Form(...), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    session = db.query(ChargingSession).filter(ChargingSession.id == session_id, ChargingSession.active == True).first()
    if not session or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="Session not found")
    cost_per_kwh = float(os.getenv("PRICE_PER_KWH", "0.2"))
    cost = energy * cost_per_kwh
    if user.balance < cost:
        session.active = False
        db.commit()
        return RedirectResponse("/dashboard", status_code=303)
    user.balance -= cost
    session.energy = energy
    session.active = False
    db.commit()
    return RedirectResponse("/dashboard", status_code=303)


@app.post("/api/session/stop")
def stop_session_api(session_id: int, energy: float, token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
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
