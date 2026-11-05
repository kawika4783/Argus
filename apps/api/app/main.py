import asyncio
import hashlib
import ipaddress
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, or_, select
from sqlalchemy.orm import Session as DbSession, sessionmaker
from .config import settings
from .demo import ensure_demo_data
from .models import Base, Case, Entity, NewsIngestionRun, NewsItem, NewsSource, Relationship, Session, User
from .security import expires_in, hash_password, new_session_id, secrets_match, verify_password

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(engine)
with SessionLocal() as db:
    ensure_demo_data(db)

app = FastAPI(title="Argus API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in settings.cors_origins.split(",")], allow_credentials=True, allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

@app.on_event("startup")
async def start_news_scheduler() -> None:
    app.state.news_scheduler_task = asyncio.create_task(_news_scheduler())

@app.on_event("shutdown")
async def stop_news_scheduler() -> None:
    task = getattr(app.state, "news_scheduler_task", None)
    if task:
        task.cancel()

class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)

class BootstrapRequest(BaseModel):
    username: str = Field(min_length=3, max_length=120)
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=12, max_length=256)
    bootstrap_secret: str = Field(min_length=1, max_length=256)

class CaseCreate(BaseModel):
    title: str = Field(min_length=3, max_length=255)
    description: str = Field(min_length=1, max_length=5000)
    priority: str = Field(default="medium", pattern="^(low|medium|high|critical)$")

class EntityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    entity_type: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=5000)
    confidence: int = Field(default=50, ge=0, le=100)
    case_id: str = Field(min_length=1, max_length=32)

class NewsSourceCreate(BaseModel):
    name: str = Field(min_length=3, max_length=255)
    url: str = Field(min_length=10, max_length=2048)
    description: str = Field(default="", max_length=5000)
    public_access: bool = True

class NewsItemAttach(BaseModel):
    case_id: str = Field(min_length=1, max_length=32)

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

def admin_user(user: User = Depends(current_user)) -> User:
    if user.role not in {"administrator", "super_administrator"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator role required")
    return user

def set_session_cookie(response: Response, user: User, db: DbSession) -> None:
    session = Session(id=new_session_id(), user_id=user.id, expires_at=expires_in(settings.session_hours))
    db.add(session)
    db.commit()
    response.set_cookie("argus_session", session.id, httponly=True, secure=settings.env == "production", samesite="lax", max_age=settings.session_hours * 3600, path="/")

@dataclass(frozen=True)
class ParsedNewsItem:
    external_id: str
    title: str
    url: str
    summary: str
    author: str
    published_at: datetime | None
    content_hash: str

class NewsSyncError(RuntimeError):
    pass

def validate_feed_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("News sources must use an http or https URL")
    hostname = parsed.hostname.lower().rstrip(".")
    if hostname in {"localhost", "localhost.localdomain"}:
        raise ValueError("Local news source hosts are not allowed")
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return value
    if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_multicast or address.is_unspecified:
        raise ValueError("Private or non-routable news source hosts are not allowed")
    return value

def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()

def _element_value(element: ET.Element, *names: str) -> str:
    wanted = {name.lower() for name in names}
    for child in list(element):
        if _local_name(child.tag) not in wanted:
            continue
        href = child.attrib.get("href", "").strip()
        text = "".join(child.itertext()).strip()
        if href:
            return href
        if text:
            return text
    return ""

def _parse_news_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed

def parse_news_feed(payload: bytes, source_url: str) -> list[ParsedNewsItem]:
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise NewsSyncError("The source did not return valid RSS or Atom XML") from exc
    entries = [element for element in root.iter() if _local_name(element.tag) in {"item", "entry"}]
    parsed_items: list[ParsedNewsItem] = []
    for entry in entries[: settings.news_max_items_per_sync]:
        title = _element_value(entry, "title")
        link = _element_value(entry, "link") or source_url
        summary = _element_value(entry, "description", "summary", "content")
        author = _element_value(entry, "author", "creator")
        published_at = _parse_news_date(_element_value(entry, "pubdate", "published", "updated", "date"))
        if not title:
            continue
        external_id = _element_value(entry, "guid", "id") or link or hashlib.sha256(f"{title}|{published_at}|{summary}".encode()).hexdigest()
        content_hash = hashlib.sha256(f"{title}\n{summary}\n{link}".encode()).hexdigest()
        parsed_items.append(ParsedNewsItem(external_id[:2048], title[:1000], link[:2048], summary, author[:255], published_at, content_hash))
    return parsed_items

def sync_news_source(db: DbSession, source: NewsSource) -> dict[str, int | str]:
    run = NewsIngestionRun(id=f"RUN-{new_session_id()[:48]}", source_id=source.id, status="running")
    db.add(run)
    db.commit()
    try:
        validate_feed_url(source.url)
        with httpx.Client(timeout=settings.news_request_timeout_seconds, follow_redirects=True, headers={"User-Agent": "Argus-NewsConnector/0.1"}) as client:
            response = client.get(source.url)
            response.raise_for_status()
            if len(response.content) > settings.news_max_feed_bytes:
                raise NewsSyncError("News feed exceeded the configured size limit")
        records = parse_news_feed(response.content, source.url)
        created = 0
        skipped = 0
        for record in records:
            existing = db.scalar(select(NewsItem).where(NewsItem.source_id == source.id, NewsItem.external_id == record.external_id))
            if existing:
                skipped += 1
                continue
            db.add(NewsItem(
                id=f"NEWS-{new_session_id()[:48]}",
                source_id=source.id,
                external_id=record.external_id,
                title=record.title,
                url=record.url,
                summary=record.summary,
                author=record.author,
                published_at=record.published_at,
                content_hash=record.content_hash,
            ))
            created += 1
        source.last_synced_at = datetime.utcnow()
        run.status = "success"
        run.finished_at = datetime.utcnow()
        run.items_seen = len(records)
        run.items_created = created
        run.items_skipped = skipped
        db.commit()
        return {"source_id": source.id, "status": run.status, "items_seen": len(records), "items_created": created, "items_skipped": skipped}
    except Exception as exc:
        db.rollback()
        failed_run = db.get(NewsIngestionRun, run.id)
        if failed_run:
            failed_run.status = "failed"
            failed_run.finished_at = datetime.utcnow()
            failed_run.error_message = str(exc)[:1000]
            db.commit()
        if isinstance(exc, NewsSyncError):
            raise
        raise NewsSyncError(f"News sync failed: {exc}") from exc

def _source_visible_to_user(source: NewsSource, user: User) -> bool:
    return user.role in {"administrator", "super_administrator"} or source.public_access or source.created_by_user_id == user.id

def _source_payload(source: NewsSource) -> dict[str, object]:
    return {"id": source.id, "name": source.name, "url": source.url, "source_type": source.source_type, "description": source.description, "enabled": source.enabled, "public_access": source.public_access, "last_synced_at": source.last_synced_at.isoformat() if source.last_synced_at else None}

def _item_payload(item: NewsItem, source: NewsSource) -> dict[str, object]:
    return {"id": item.id, "source_id": source.id, "source_name": source.name, "external_id": item.external_id, "title": item.title, "url": item.url, "summary": item.summary, "author": item.author, "published_at": item.published_at.isoformat() if item.published_at else None, "discovered_at": item.discovered_at.isoformat(), "case_id": item.case_id, "review_status": item.review_status}

def run_scheduled_news_sync() -> None:
    with SessionLocal() as db:
        sources = db.scalars(select(NewsSource).where(NewsSource.enabled.is_(True))).all()
        for source in sources:
            try:
                sync_news_source(db, source)
            except NewsSyncError:
                continue

async def _news_scheduler() -> None:
    while True:
        await asyncio.sleep(settings.news_poll_interval_seconds)
        await asyncio.to_thread(run_scheduled_news_sync)

@app.get("/health")
def health():
    return {"status": "ok", "service": "argus-api"}

@app.post("/auth/login")
def login(payload: LoginRequest, response: Response, db: DbSession = Depends(db_session)):
    user = db.scalar(select(User).where((User.username == payload.username) | (User.email == payload.username)))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    set_session_cookie(response, user, db)
    return {"user": {"username": user.username, "role": user.role}}

@app.get("/admin/bootstrap-status")
def bootstrap_status(db: DbSession = Depends(db_session)):
    has_admin = db.scalar(select(User).where(User.role.in_(["administrator", "super_administrator"]))) is not None
    return {"available": bool(settings.bootstrap_secret) and not has_admin}

@app.post("/admin/bootstrap")
def bootstrap(payload: BootstrapRequest, response: Response, db: DbSession = Depends(db_session)):
    if not secrets_match(payload.bootstrap_secret, settings.bootstrap_secret):
        raise HTTPException(status_code=403, detail="Invalid bootstrap secret")
    if db.scalar(select(User).where(User.role.in_(["administrator", "super_administrator"]))) is not None:
        raise HTTPException(status_code=409, detail="Administrator already provisioned")
    if db.scalar(select(User).where((User.username == payload.username) | (User.email == payload.email))):
        raise HTTPException(status_code=409, detail="Username or email already exists")
    user = User(username=payload.username, email=payload.email, password_hash=hash_password(payload.password), role="super_administrator")
    db.add(user)
    db.flush()
    set_session_cookie(response, user, db)
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

@app.get("/cases")
def list_cases(user: User = Depends(current_user), db: DbSession = Depends(db_session)):
    return [{"id": c.id, "title": c.title, "description": c.description, "status": c.status, "priority": c.priority} for c in db.scalars(select(Case).where(Case.owner_user_id == user.id)).all()]

@app.post("/cases")
def create_case(payload: CaseCreate, user: User = Depends(current_user), db: DbSession = Depends(db_session)):
    case_id = f"CS-{new_session_id()[:8].upper()}"
    case = Case(id=case_id, title=payload.title, description=payload.description, status="active", priority=payload.priority, owner_user_id=user.id)
    db.add(case)
    db.commit()
    return {"id": case.id, "title": case.title, "description": case.description, "status": case.status, "priority": case.priority}

@app.post("/entities")
def create_entity(payload: EntityCreate, user: User = Depends(current_user), db: DbSession = Depends(db_session)):
    case = db.scalar(select(Case).where(Case.id == payload.case_id, Case.owner_user_id == user.id))
    if not case:
        raise HTTPException(status_code=404, detail="Authorized case not found")
    entity_id = f"E-{new_session_id()[:8].upper()}"
    entity = Entity(id=entity_id, name=payload.name, entity_type=payload.entity_type, description=payload.description, confidence=payload.confidence, case_id=case.id)
    db.add(entity)
    db.commit()
    return {"id": entity.id, "name": entity.name, "type": entity.entity_type, "confidence": entity.confidence, "case_id": entity.case_id}

@app.get("/admin/access-check")
def access_check(user: User = Depends(admin_user)):
    return {"username": user.username, "role": user.role, "administrator": True}

@app.get("/news/sources")
def list_news_sources(user: User = Depends(current_user), db: DbSession = Depends(db_session)):
    return [_source_payload(source) for source in db.scalars(select(NewsSource).where(NewsSource.enabled.is_(True))).all() if _source_visible_to_user(source, user)]

@app.get("/news/items")
def list_news_items(case_id: str | None = None, user: User = Depends(current_user), db: DbSession = Depends(db_session)):
    is_admin = user.role in {"administrator", "super_administrator"}
    if case_id and not is_admin and not db.scalar(select(Case).where(Case.id == case_id, Case.owner_user_id == user.id)):
        raise HTTPException(status_code=404, detail="Authorized case not found")
    statement = select(NewsItem, NewsSource).join(NewsSource, NewsItem.source_id == NewsSource.id).where(NewsSource.enabled.is_(True))
    if not is_admin:
        owned_case_ids = select(Case.id).where(Case.owner_user_id == user.id)
        statement = statement.where(NewsSource.public_access.is_(True), or_(NewsItem.case_id.is_(None), NewsItem.case_id.in_(owned_case_ids)))
    if case_id:
        statement = statement.where(NewsItem.case_id == case_id)
    rows = db.execute(statement.order_by(NewsItem.published_at.desc(), NewsItem.discovered_at.desc()).limit(100)).all()
    return [_item_payload(item, source) for item, source in rows]

@app.post("/news/sources/{source_id}/sync")
def sync_news(source_id: str, user: User = Depends(current_user), db: DbSession = Depends(db_session)):
    source = db.get(NewsSource, source_id)
    if not source or not source.enabled or not _source_visible_to_user(source, user):
        raise HTTPException(status_code=404, detail="Authorized news source not found")
    try:
        return sync_news_source(db, source)
    except NewsSyncError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

@app.post("/news/items/{item_id}/attach")
def attach_news_item(item_id: str, payload: NewsItemAttach, user: User = Depends(current_user), db: DbSession = Depends(db_session)):
    item = db.get(NewsItem, item_id)
    case = db.scalar(select(Case).where(Case.id == payload.case_id, Case.owner_user_id == user.id))
    if not item or not case:
        raise HTTPException(status_code=404, detail="Authorized news item or case not found")
    source = db.get(NewsSource, item.source_id)
    if not source or not _source_visible_to_user(source, user):
        raise HTTPException(status_code=404, detail="Authorized news item not found")
    item.case_id = case.id
    item.review_status = "attached"
    db.commit()
    return {"id": item.id, "case_id": item.case_id, "review_status": item.review_status}

@app.get("/admin/news/runs")
def list_news_runs(user: User = Depends(admin_user), db: DbSession = Depends(db_session)):
    runs = db.scalars(select(NewsIngestionRun).order_by(NewsIngestionRun.started_at.desc()).limit(50)).all()
    return [{"id": run.id, "source_id": run.source_id, "status": run.status, "started_at": run.started_at.isoformat(), "finished_at": run.finished_at.isoformat() if run.finished_at else None, "items_seen": run.items_seen, "items_created": run.items_created, "items_skipped": run.items_skipped, "error_message": run.error_message} for run in runs]

@app.post("/admin/news/sources")
def create_news_source(payload: NewsSourceCreate, user: User = Depends(admin_user), db: DbSession = Depends(db_session)):
    try:
        validate_feed_url(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    source = NewsSource(id=f"SRC-{new_session_id()[:12].upper()}", name=payload.name, url=payload.url, description=payload.description, public_access=payload.public_access, created_by_user_id=user.id)
    db.add(source)
    db.commit()
    return _source_payload(source)
