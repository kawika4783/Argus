import asyncio
import hashlib
import ipaddress
import json
import math
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterator
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import create_engine, func, or_, select
from sqlalchemy.orm import Session as DbSession, sessionmaker
from .config import settings
from .demo import ensure_demo_data
from .models import AiProviderSetting, AiUsageEvent, Base, Case, Entity, NewsIngestionRun, NewsItem, NewsSource, Relationship, Session, User
from .security import expires_in, hash_password, new_session_id, secrets_match, verify_password

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base.metadata.create_all(engine)
with SessionLocal() as db:
    ensure_demo_data(db)

app = FastAPI(title="Argus API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=[x.strip() for x in settings.cors_origins.split(",")], allow_credentials=True, allow_methods=["GET", "POST", "PATCH"], allow_headers=["Content-Type"])

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

class RelationshipCreate(BaseModel):
    source_id: str = Field(min_length=1, max_length=32)
    target_id: str = Field(min_length=1, max_length=32)
    relationship_type: str = Field(min_length=2, max_length=64)
    confidence: int = Field(default=50, ge=0, le=100)
    case_id: str = Field(min_length=1, max_length=32)

class AiSettingsUpdate(BaseModel):
    zero_cost_mode: bool | None = None
    active_provider: str | None = Field(default=None, pattern="^(deterministic|ollama|openai)$")
    ollama_model: str | None = Field(default=None, max_length=128)
    context_limit: int | None = Field(default=None, ge=512, le=32768)
    openai_enabled: bool | None = None
    openai_model: str | None = Field(default=None, max_length=128)
    openai_spending_limit_usd: float | None = Field(default=None, ge=0, le=100000)

class AiQuestionRequest(BaseModel):
    question: str = Field(min_length=3, max_length=10000)
    case_id: str | None = Field(default=None, max_length=32)
    analysis_type: str = Field(default="case_summary", pattern="^(case_summary|trend|anomaly|monte_carlo|regression|question)$")
    values: list[float] = Field(default_factory=list, max_length=5000)
    trials: int = Field(default=1000, ge=100, le=100000)
    probability: float = Field(default=0.5, ge=0, le=1)

class AnalysisRequest(BaseModel):
    analysis_type: str = Field(pattern="^(case_summary|trend|anomaly|monte_carlo|regression)$")
    values: list[float] = Field(default_factory=list, max_length=5000)
    trials: int = Field(default=1000, ge=100, le=100000)
    probability: float = Field(default=0.5, ge=0, le=1)

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

def _get_ai_settings(db: DbSession) -> AiProviderSetting:
    record = db.get(AiProviderSetting, "DEFAULT")
    if record:
        return record
    record = AiProviderSetting(id="DEFAULT", zero_cost_mode=settings.zero_cost_mode, active_provider="deterministic", ollama_model=settings.ollama_default_model, context_limit=settings.ollama_context_limit, openai_enabled=False, openai_model=settings.openai_default_model, openai_spending_limit_usd=settings.openai_spending_limit_usd)
    db.add(record)
    db.commit()
    return record

def _ollama_models() -> dict[str, object]:
    endpoint = settings.ollama_base_url.rstrip("/") + "/api/tags"
    try:
        headers = {"Authorization": f"Bearer {settings.ollama_api_key}"} if settings.ollama_api_key else {}
        with httpx.Client(timeout=2.0, headers=headers) as client:
            response = client.get(endpoint)
            response.raise_for_status()
        models = [item.get("name", "") for item in response.json().get("models", []) if item.get("name")]
        return {"available": True, "models": models, "error": None}
    except Exception as exc:
        return {"available": False, "models": [], "error": f"Ollama unavailable: {type(exc).__name__}"}

def _usage_summary(db: DbSession) -> dict[str, object]:
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    external = db.scalar(select(func.count(AiUsageEvent.id)).where(AiUsageEvent.created_at >= month_start, AiUsageEvent.provider.in_(["openai", "chatgpt"]))) or 0
    estimated = db.scalar(select(func.coalesce(func.sum(AiUsageEvent.estimated_cost_usd), 0.0)).where(AiUsageEvent.created_at >= month_start)) or 0.0
    local = db.scalar(select(func.count(AiUsageEvent.id)).where(AiUsageEvent.created_at >= month_start, AiUsageEvent.provider.in_(["deterministic", "ollama"]))) or 0
    errors = db.scalar(select(func.count(AiUsageEvent.id)).where(AiUsageEvent.created_at >= month_start, AiUsageEvent.status == "error")) or 0
    return {"month": month_start.strftime("%Y-%m"), "local_requests": local, "external_requests": external, "estimated_cost_usd": round(float(estimated), 6), "provider_errors": errors}

def _ai_status_payload(db: DbSession) -> dict[str, object]:
    record = _get_ai_settings(db)
    ollama = _ollama_models()
    openai_configured = bool(settings.openai_enabled and settings.openai_api_key and record.openai_enabled and not record.zero_cost_mode)
    if record.zero_cost_mode:
        active_provider = "ollama" if record.active_provider == "ollama" and ollama["available"] else "deterministic"
    elif record.active_provider == "openai" and openai_configured:
        active_provider = "openai"
    elif record.active_provider == "ollama" and ollama["available"]:
        active_provider = "ollama"
    else:
        active_provider = "deterministic"
    return {
        "zero_cost_mode": record.zero_cost_mode,
        "active_provider": active_provider,
        "configured_provider": record.active_provider,
        "model": record.ollama_model if active_provider == "ollama" else record.openai_model if active_provider == "openai" else "deterministic-engine",
        "context_limit": record.context_limit,
        "ollama": {"available": ollama["available"], "models": ollama["models"], "error": ollama["error"], "endpoint": settings.ollama_base_url},
        "openai": {"enabled_by_admin": record.openai_enabled, "enabled_by_environment": settings.openai_enabled, "api_key_configured": bool(settings.openai_api_key), "available": openai_configured, "spending_limit_usd": record.openai_spending_limit_usd},
        "chatgpt_subscription": {"status": "unavailable_remote_hosted_approval_required", "message": "Official ChatGPT plan usage requires an approved integration; it is not enabled for this remotely hosted Argus instance."},
        "usage": _usage_summary(db),
    }

def _authorized_case(case_id: str | None, user: User, db: DbSession) -> Case | None:
    if not case_id:
        return None
    is_admin = user.role in {"administrator", "super_administrator"}
    case = db.get(Case, case_id)
    if not case or (not is_admin and case.owner_user_id != user.id):
        raise HTTPException(status_code=404, detail="Authorized case not found")
    return case

def _deterministic_analysis(case: Case | None, payload: AnalysisRequest | AiQuestionRequest, db: DbSession) -> dict[str, object]:
    if payload.analysis_type == "monte_carlo":
        rng = random.Random(42)
        successes = sum(1 for _ in range(payload.trials) if rng.random() < payload.probability)
        estimate = successes / payload.trials
        margin = 1.96 * math.sqrt(max(estimate * (1 - estimate), 1e-9) / payload.trials)
        return {"engine": "deterministic", "ai_generated": False, "analysis_type": payload.analysis_type, "answer": f"Monte Carlo estimate: {estimate:.3f} success probability over {payload.trials:,} trials.", "metrics": {"trials": payload.trials, "successes": successes, "estimate": round(estimate, 6), "confidence_interval_95": [round(max(0, estimate - margin), 6), round(min(1, estimate + margin), 6)]}, "evidence": []}
    if payload.analysis_type == "regression":
        if len(payload.values) < 2:
            raise HTTPException(status_code=422, detail="Regression requires at least two numeric observations")
        xs = list(range(len(payload.values)))
        x_mean = sum(xs) / len(xs)
        y_mean = sum(payload.values) / len(payload.values)
        denominator = sum((x - x_mean) ** 2 for x in xs)
        slope = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, payload.values)) / denominator
        intercept = y_mean - slope * x_mean
        ss_total = sum((y - y_mean) ** 2 for y in payload.values)
        ss_residual = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, payload.values))
        r_squared = 1 - ss_residual / ss_total if ss_total else 1.0
        return {"engine": "deterministic", "ai_generated": False, "analysis_type": payload.analysis_type, "answer": f"Linear trend slope: {slope:.4f}; R²: {r_squared:.3f}.", "metrics": {"slope": round(slope, 6), "intercept": round(intercept, 6), "r_squared": round(r_squared, 6), "observations": len(payload.values)}, "evidence": []}
    if not case:
        return {"engine": "deterministic", "ai_generated": False, "analysis_type": payload.analysis_type, "answer": "No case was selected. Choose a case or provide a numeric analysis input.", "metrics": {}, "evidence": []}
    entities = db.scalars(select(Entity).where(Entity.case_id == case.id)).all()
    relationships = db.scalars(select(Relationship).where(Relationship.case_id == case.id)).all()
    news = db.scalars(select(NewsItem).where(NewsItem.case_id == case.id)).all()
    degree: dict[str, int] = {entity.id: 0 for entity in entities}
    for relationship in relationships:
        degree[relationship.source_id] = degree.get(relationship.source_id, 0) + 1
        degree[relationship.target_id] = degree.get(relationship.target_id, 0) + 1
    if payload.analysis_type == "trend":
        daily: dict[str, int] = {}
        for item in news:
            date_key = (item.published_at or item.discovered_at).date().isoformat()
            daily[date_key] = daily.get(date_key, 0) + 1
        return {"engine": "deterministic", "ai_generated": False, "analysis_type": payload.analysis_type, "answer": f"The case has {len(news)} attached news records across {len(daily)} observed dates.", "metrics": {"daily_news": daily, "news_items": len(news)}, "evidence": [item.id for item in news]}
    if payload.analysis_type == "anomaly":
        low_confidence = [entity.id for entity in entities if entity.confidence < 50]
        isolated = [entity.id for entity in entities if degree.get(entity.id, 0) == 0]
        return {"engine": "deterministic", "ai_generated": False, "analysis_type": payload.analysis_type, "answer": f"Found {len(low_confidence)} low-confidence entities and {len(isolated)} isolated entities for analyst review.", "metrics": {"low_confidence_entities": low_confidence, "isolated_entities": isolated}, "evidence": low_confidence + isolated}
    top = sorted(((entity_id, count) for entity_id, count in degree.items()), key=lambda item: item[1], reverse=True)[:5]
    answer = f"Case {case.id} contains {len(entities)} entities, {len(relationships)} relationships, and {len(news)} attached news records."
    if payload.analysis_type == "question":
        answer += " This is a deterministic case summary; no paid or external model was used."
    return {"engine": "deterministic", "ai_generated": False, "analysis_type": payload.analysis_type, "answer": answer, "metrics": {"entities": len(entities), "relationships": len(relationships), "news_items": len(news), "highest_degree_entities": top}, "evidence": [case.id] + [entity.id for entity in entities]}

def _case_context(case: Case | None, db: DbSession, context_limit: int) -> str:
    if not case:
        return "No case was selected."
    entities = db.scalars(select(Entity).where(Entity.case_id == case.id).limit(50)).all()
    relationships = db.scalars(select(Relationship).where(Relationship.case_id == case.id).limit(50)).all()
    news = db.scalars(select(NewsItem).where(NewsItem.case_id == case.id).limit(20)).all()
    context = f"Case {case.id}: {case.title}\nDescription: {case.description}\nEntities: " + ", ".join(f"{item.name} ({item.entity_type})" for item in entities) + "\nRelationships: " + ", ".join(f"{item.source_id} {item.relationship_type} {item.target_id}" for item in relationships) + "\nAttached news: " + ", ".join(item.title for item in news)
    return context[: max(1000, context_limit * 4)]

def _provider_root(base_url: str) -> str:
    root = base_url.rstrip("/")
    return root[:-3] if root.endswith("/v1") else root

def _chat_completion(base_url: str, model: str, api_key: str, messages: list[dict[str, str]], timeout: int) -> tuple[str, int, int]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    root = _provider_root(base_url)
    with httpx.Client(timeout=timeout) as client:
        response = client.post(root + "/v1/chat/completions", headers=headers, json={"model": model, "messages": messages, "stream": False})
        if response.status_code not in {404, 405}:
            response.raise_for_status()
            body = response.json()
            text = body.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = body.get("usage", {})
            return text, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
        response = client.post(root + "/api/chat", headers=headers, json={"model": model, "messages": messages, "stream": False})
        response.raise_for_status()
        body = response.json()
        message = body.get("message", {})
        return str(message.get("content", "")), 0, 0

def _ollama_stream(base_url: str, model: str, api_key: str, messages: list[dict[str, str]], timeout: int) -> Iterator[str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    root = _provider_root(base_url)
    with httpx.Client(timeout=timeout) as client:
        with client.stream("POST", root + "/v1/chat/completions", headers=headers, json={"model": model, "messages": messages, "stream": True}) as response:
            if response.status_code in {404, 405}:
                response.close()
            else:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    raw = line[5:].strip() if line.startswith("data:") else line
                    if raw == "[DONE]":
                        return
                    try:
                        body = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    text = body.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if text:
                        yield text
                return
        with client.stream("POST", root + "/api/chat", headers=headers, json={"model": model, "messages": messages, "stream": True}) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                try:
                    body = json.loads(line)
                except json.JSONDecodeError:
                    continue
                text = body.get("message", {}).get("content", "")
                if text:
                    yield text
                if body.get("done"):
                    return

def _record_ai_usage(db: DbSession, user: User, provider: str, model: str, operation: str, status_value: str, input_tokens: int = 0, output_tokens: int = 0, error_message: str = "") -> None:
    estimated = ((input_tokens + output_tokens) / 1000) * settings.openai_cost_per_1k_tokens_usd if provider == "openai" else 0.0
    db.add(AiUsageEvent(id=f"AI-{new_session_id()[:48]}", user_id=user.id, provider=provider, model=model, operation=operation, status=status_value, input_tokens=input_tokens, output_tokens=output_tokens, estimated_cost_usd=estimated, error_message=error_message[:1000]))
    db.commit()

def _run_ai_question(payload: AiQuestionRequest, user: User, db: DbSession) -> dict[str, object]:
    case = _authorized_case(payload.case_id, user, db)
    deterministic_payload = AnalysisRequest(analysis_type=payload.analysis_type if payload.analysis_type != "question" else "question", values=payload.values, trials=payload.trials, probability=payload.probability)
    record = _get_ai_settings(db)
    status_payload = _ai_status_payload(db)
    if payload.analysis_type != "question" or status_payload["active_provider"] == "deterministic":
        result = _deterministic_analysis(case, deterministic_payload, db)
        result["question"] = payload.question
        _record_ai_usage(db, user, "deterministic", "deterministic-engine", "question", "success")
        return result
    provider = str(status_payload["active_provider"])
    model = str(status_payload["model"])
    try:
        if provider == "openai":
            current_cost = float(_usage_summary(db)["estimated_cost_usd"])
            if record.openai_spending_limit_usd <= 0 or current_cost >= record.openai_spending_limit_usd:
                raise RuntimeError("OpenAI usage is blocked by the configured spending limit")
            base_url, api_key = settings.openai_base_url, settings.openai_api_key
        else:
            base_url, api_key = settings.ollama_base_url, settings.ollama_api_key
        text, input_tokens, output_tokens = _chat_completion(base_url, model, api_key, [{"role": "system", "content": "Answer only from the supplied authorized case context. If evidence is missing, say so. Label uncertainty."}, {"role": "user", "content": payload.question + "\n\nAUTHORIZED CASE CONTEXT:\n" + _case_context(case, db, record.context_limit)}], settings.ollama_request_timeout_seconds)
        _record_ai_usage(db, user, provider, model, "question", "success", input_tokens, output_tokens)
        return {"engine": provider, "ai_generated": True, "provider_used": provider, "model": model, "question": payload.question, "answer": text, "evidence": [case.id] if case else []}
    except Exception as exc:
        _record_ai_usage(db, user, provider, model, "question", "error", error_message=type(exc).__name__)
        fallback = _deterministic_analysis(case, deterministic_payload, db)
        fallback["provider_requested"] = provider
        fallback["fallback_reason"] = "Configured provider unavailable; deterministic analysis was used instead."
        fallback["question"] = payload.question
        return fallback

def _stream_chunks(value: dict[str, object]) -> Iterator[str]:
    text_value = str(value.get("answer", ""))
    words = text_value.split(" ") or [text_value]
    for index in range(0, len(words), 24):
        chunk = " ".join(words[index:index + 24])
        yield f"data: {json.dumps({'type': 'answer_delta', 'text': chunk + (' ' if index + 24 < len(words) else '')})}\n\n"
    yield f"data: {json.dumps({'type': 'complete', 'result': value})}\n\n"
    yield "data: [DONE]\n\n"

@app.get("/health")
def health():
    return {"status": "ok", "service": "argus-api"}

@app.get("/ai/status")
def ai_status(user: User = Depends(current_user), db: DbSession = Depends(db_session)):
    return _ai_status_payload(db)

@app.post("/ai/question")
def ai_question(payload: AiQuestionRequest, user: User = Depends(current_user), db: DbSession = Depends(db_session)):
    return _run_ai_question(payload, user, db)

@app.post("/ai/question/stream")
def ai_question_stream(payload: AiQuestionRequest, user: User = Depends(current_user), db: DbSession = Depends(db_session)):
    case = _authorized_case(payload.case_id, user, db)
    status_payload = _ai_status_payload(db)
    deterministic_payload = AnalysisRequest(analysis_type=payload.analysis_type if payload.analysis_type != "question" else "question", values=payload.values, trials=payload.trials, probability=payload.probability)
    if payload.analysis_type != "question" or status_payload["active_provider"] == "deterministic":
        result = _deterministic_analysis(case, deterministic_payload, db)
        result["question"] = payload.question
        _record_ai_usage(db, user, "deterministic", "deterministic-engine", "question", "success")
        return StreamingResponse(_stream_chunks(result), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    provider = str(status_payload["active_provider"])
    model = str(status_payload["model"])
    record = _get_ai_settings(db)
    base_url = settings.ollama_base_url if provider == "ollama" else settings.openai_base_url
    api_key = settings.ollama_api_key if provider == "ollama" else settings.openai_api_key
    messages = [{"role": "system", "content": "Answer only from the supplied authorized case context. If evidence is missing, say so. Label uncertainty."}, {"role": "user", "content": payload.question + "\n\nAUTHORIZED CASE CONTEXT:\n" + _case_context(case, db, record.context_limit)}]

    def generate() -> Iterator[str]:
        chunks: list[str] = []
        try:
            for text in _ollama_stream(base_url, model, api_key, messages, settings.ollama_request_timeout_seconds):
                chunks.append(text)
                yield f"data: {json.dumps({'type': 'answer_delta', 'text': text})}\n\n"
            answer = "".join(chunks)
            _record_ai_usage(db, user, provider, model, "question", "success", 0, len(answer.split()))
            yield f"data: {json.dumps({'type': 'complete', 'result': {'engine': provider, 'provider_used': provider, 'model': model, 'ai_generated': True, 'question': payload.question, 'answer': answer, 'evidence': [case.id] if case else []}})}\n\n"
        except Exception as exc:
            _record_ai_usage(db, user, provider, model, "question", "error", error_message=type(exc).__name__)
            fallback = _deterministic_analysis(case, deterministic_payload, db)
            fallback["question"] = payload.question
            fallback["provider_requested"] = provider
            fallback["fallback_reason"] = "Ollama was unavailable; deterministic analysis was used instead."
            yield from _stream_chunks(fallback)
    return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@app.post("/analysis/cases/{case_id}")
def case_analysis(case_id: str, payload: AnalysisRequest, user: User = Depends(current_user), db: DbSession = Depends(db_session)):
    case = _authorized_case(case_id, user, db)
    return _deterministic_analysis(case, payload, db)

@app.get("/admin/ai/settings")
def admin_ai_settings(user: User = Depends(admin_user), db: DbSession = Depends(db_session)):
    return _ai_status_payload(db)

@app.patch("/admin/ai/settings")
def update_admin_ai_settings(payload: AiSettingsUpdate, user: User = Depends(admin_user), db: DbSession = Depends(db_session)):
    record = _get_ai_settings(db)
    zero_cost_mode = record.zero_cost_mode if payload.zero_cost_mode is None else payload.zero_cost_mode
    if payload.active_provider == "openai" and zero_cost_mode:
        raise HTTPException(status_code=422, detail="Zero Additional Cost Mode blocks paid providers")
    if payload.openai_enabled and zero_cost_mode:
        raise HTTPException(status_code=422, detail="Disable Zero Additional Cost Mode before enabling OpenAI")
    if payload.openai_enabled and (not settings.openai_enabled or not settings.openai_api_key):
        raise HTTPException(status_code=422, detail="OpenAI is disabled until the server environment explicitly enables it and provides a key")
    if payload.zero_cost_mode is True:
        record.openai_enabled = False
    if payload.zero_cost_mode is not None:
        record.zero_cost_mode = payload.zero_cost_mode
    if payload.active_provider is not None:
        record.active_provider = payload.active_provider
    if payload.ollama_model is not None:
        record.ollama_model = payload.ollama_model.strip()
    if payload.context_limit is not None:
        record.context_limit = payload.context_limit
    if payload.openai_enabled is not None:
        record.openai_enabled = payload.openai_enabled
    if payload.openai_model is not None:
        record.openai_model = payload.openai_model.strip()
    if payload.openai_spending_limit_usd is not None:
        record.openai_spending_limit_usd = payload.openai_spending_limit_usd
    record.updated_at = datetime.utcnow()
    db.commit()
    return _ai_status_payload(db)

@app.get("/admin/ai/ollama-models")
def admin_ollama_models(user: User = Depends(admin_user)):
    return _ollama_models()

@app.get("/admin/ai/usage")
def admin_ai_usage(user: User = Depends(admin_user), db: DbSession = Depends(db_session)):
    events = db.scalars(select(AiUsageEvent).order_by(AiUsageEvent.created_at.desc()).limit(100)).all()
    return {"summary": _usage_summary(db), "events": [{"id": event.id, "provider": event.provider, "model": event.model, "operation": event.operation, "status": event.status, "estimated_cost_usd": event.estimated_cost_usd, "created_at": event.created_at.isoformat(), "error_message": event.error_message} for event in events]}

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

@app.get("/relationships")
def list_relationships(case_id: str | None = None, user: User = Depends(current_user), db: DbSession = Depends(db_session)):
    is_admin = user.role in {"administrator", "super_administrator"}
    if case_id and not is_admin and not db.scalar(select(Case).where(Case.id == case_id, Case.owner_user_id == user.id)):
        raise HTTPException(status_code=404, detail="Authorized case not found")
    statement = select(Relationship)
    if not is_admin:
        owned_case_ids = select(Case.id).where(Case.owner_user_id == user.id)
        statement = statement.where(Relationship.case_id.in_(owned_case_ids))
    if case_id:
        statement = statement.where(Relationship.case_id == case_id)
    return [{"id": relationship.id, "source_id": relationship.source_id, "target_id": relationship.target_id, "type": relationship.relationship_type, "confidence": relationship.confidence, "case_id": relationship.case_id} for relationship in db.scalars(statement.order_by(Relationship.id)).all()]

@app.post("/relationships")
def create_relationship(payload: RelationshipCreate, user: User = Depends(current_user), db: DbSession = Depends(db_session)):
    if payload.source_id == payload.target_id:
        raise HTTPException(status_code=422, detail="A relationship must connect two different entities")
    case = db.scalar(select(Case).where(Case.id == payload.case_id, Case.owner_user_id == user.id))
    if not case:
        raise HTTPException(status_code=404, detail="Authorized case not found")
    entities = db.scalars(select(Entity).where(Entity.id.in_([payload.source_id, payload.target_id]), Entity.case_id == case.id)).all()
    if len(entities) != 2:
        raise HTTPException(status_code=404, detail="Both entities must belong to the authorized case")
    relationship = Relationship(id=f"R-{new_session_id()[:8].upper()}", source_id=payload.source_id, target_id=payload.target_id, relationship_type=payload.relationship_type.strip(), confidence=payload.confidence, case_id=case.id)
    db.add(relationship)
    db.commit()
    return {"id": relationship.id, "source_id": relationship.source_id, "target_id": relationship.target_id, "type": relationship.relationship_type, "confidence": relationship.confidence, "case_id": relationship.case_id}

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
