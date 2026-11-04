from datetime import datetime
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session as DbSession, sessionmaker
from .config import settings
from .demo import ensure_demo_data
from .models import Base, Case, Entity, Relationship, Session, User
from .security import expires_in, new_session_id, verify_password

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(engine)
with SessionLocal() as db:
    ensure_demo_data(db)

app = FastAPI(title="Argus API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in settings.cors_origins.split(",")], allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)

def db_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def current_user(request: Request, db: DbSession = Depends(db_session)) -> User:
    session_id = request.cookies.get("argus_session")
    if not session_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    record = db.get(Session, session_id)
    if not record or record.revoked_at or record.expires_at <= datetime.utcnow():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    user = db.get(User, record.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User disabled")
    return user

@app.get("/health")
def health():
    return {"status": "ok", "service": "argus-api"}

@app.post("/auth/login")
def login(payload: LoginRequest, response: Response, db: DbSession = Depends(db_session)):
    user = db.scalar(select(User).where((User.username == payload.username) | (User.email == payload.username)))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    session = Session(id=new_session_id(), user_id=user.id, expires_at=expires_in(settings.session_hours))
    db.add(session)
    db.commit()
    response.set_cookie("argus_session", session.id, httponly=True, secure=settings.env == "production", samesite="lax", max_age=settings.session_hours * 3600, path="/")
    return {"user": {"username": user.username, "role": user.role}}

@app.post("/auth/logout")
def logout(request: Request, response: Response, db: DbSession = Depends(db_session)):
    session_id = request.cookies.get("argus_session")
    if session_id:
        record = db.get(Session, session_id)
        if record:
            record.revoked_at = datetime.utcnow()
            db.commit()
    response.delete_cookie("argus_session", path="/")
    return {"ok": True}

@app.get("/auth/me")
def me(user: User = Depends(current_user)):
    return {"username": user.username, "email": user.email, "role": user.role}

@app.get("/dashboard")
def dashboard(user: User = Depends(current_user), db: DbSession = Depends(db_session)):
    cases = db.scalars(select(Case).where(Case.owner_user_id == user.id)).all()
    case_ids = [c.id for c in cases]
    entities = db.scalars(select(Entity).where(Entity.case_id.in_(case_ids))).all() if case_ids else []
    relationships = db.scalars(select(Relationship).where(Relationship.case_id.in_(case_ids))).all() if case_ids else []
    return {
        "stats": {"active_cases": len([c for c in cases if c.status == "active"]), "entities": len(entities), "relationships": len(relationships), "evidence_ingestion": 96, "alerts": 7},
        "cases": [{"id": c.id, "title": c.title, "status": c.status, "priority": c.priority, "description": c.description} for c in cases],
        "entities": [{"id": e.id, "name": e.name, "type": e.entity_type, "confidence": e.confidence} for e in entities],
        "relationships": [{"id": r.id, "source": r.source_id, "target": r.target_id, "type": r.relationship_type, "confidence": r.confidence} for r in relationships],
        "activity": ["New relationship discovered", "Evidence ingested: 1,248 records", "Entity updated: E-18472", "Case note added"],
        "alerts": ["High-risk relationship detected", "Unusual transaction cluster", "New entity match (watchlist)"],
        "tasks": ["Review new entity matches", "Analyze transaction cluster", "Validate relationships"],
    }

@app.get("/entities")
def list_entities(user: User = Depends(current_user), db: DbSession = Depends(db_session)):
    case_ids = [c.id for c in db.scalars(select(Case).where(Case.owner_user_id == user.id)).all()]
    if not case_ids:
        return []
    return [{"id": e.id, "name": e.name, "type": e.entity_type, "confidence": e.confidence, "case_id": e.case_id} for e in db.scalars(select(Entity).where(Entity.case_id.in_(case_ids))).all()]
