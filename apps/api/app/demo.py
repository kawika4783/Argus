from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession
from .config import settings
from .models import AiProviderSetting, Case, Entity, NewsSource, Relationship, User
from .security import hash_password

def ensure_demo_data(db: DbSession) -> None:
    ai_settings = db.get(AiProviderSetting, "DEFAULT")
    if not ai_settings:
        db.add(AiProviderSetting(id="DEFAULT", zero_cost_mode=settings.zero_cost_mode, active_provider="ollama" if settings.ollama_default_model and settings.ollama_base_url != "http://ollama:11434" else "deterministic", ollama_model=settings.ollama_default_model, context_limit=settings.ollama_context_limit, openai_enabled=False, openai_model=settings.openai_default_model, openai_spending_limit_usd=settings.openai_spending_limit_usd))
    else:
        if not ai_settings.ollama_model and settings.ollama_default_model:
            ai_settings.ollama_model = settings.ollama_default_model
        if ai_settings.active_provider == "deterministic" and settings.ollama_default_model and settings.ollama_base_url != "http://ollama:11434":
            ai_settings.active_provider = "ollama"
    user = db.scalar(select(User).where(User.username == "analyst"))
    if not user:
        user = User(username="analyst", email="analyst@nexusintel.local", password_hash=hash_password(settings.demo_password), role="analyst")
        db.add(user)
        db.flush()
        case = Case(id="CS-4729", title="Network Activity Investigation", description="Synthetic analysis of connected entities and communications.", status="active", priority="high", owner_user_id=user.id)
        db.add(case)
        items = [
            Entity(id="E-18472", name="Aster Group", entity_type="Organization", description="Synthetic organization record", confidence=86, case_id=case.id),
            Entity(id="E-18473", name="Mara Voss", entity_type="Person", description="Synthetic person record", confidence=78, case_id=case.id),
            Entity(id="E-18474", name="North Harbor", entity_type="Location", description="Synthetic location record", confidence=91, case_id=case.id),
            Entity(id="E-18475", name="Vehicle 7F2", entity_type="Vehicle", description="Synthetic asset record", confidence=67, case_id=case.id),
            Entity(id="E-18476", name="Signal Device 14", entity_type="Device", description="Synthetic device record", confidence=72, case_id=case.id),
        ]
        db.add_all(items)
        db.flush()
        db.add_all([
            Relationship(id="R-1001", source_id="E-18473", target_id="E-18472", relationship_type="Associated With", confidence=84, case_id=case.id),
            Relationship(id="R-1002", source_id="E-18473", target_id="E-18474", relationship_type="Located At", confidence=79, case_id=case.id),
            Relationship(id="R-1003", source_id="E-18472", target_id="E-18475", relationship_type="Owns", confidence=65, case_id=case.id),
            Relationship(id="R-1004", source_id="E-18476", target_id="E-18473", relationship_type="Connected To", confidence=73, case_id=case.id),
        ])
    source = db.scalar(select(NewsSource).where(NewsSource.id == "SRC-GDELT"))
    if not source:
        db.add(NewsSource(
            id="SRC-GDELT",
            name="GDELT Global News",
            url="https://data.gdeltproject.org/gdeltv3/gal/feed.rss",
            source_type="rss",
            description="Free public global news and event feed for synthetic demonstration and analyst review.",
            enabled=True,
            public_access=True,
        ))
    elif source.url.startswith("https://api.gdeltproject.org/api/v2/doc/doc"):
        source.url = "https://data.gdeltproject.org/gdeltv3/gal/feed.rss"
        source.description = "Free public global news and event feed for synthetic demonstration and analyst review."
    db.commit()
