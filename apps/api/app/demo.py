from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession
from .config import settings
from .models import Case, Entity, NewsSource, Relationship, User
from .security import hash_password

def ensure_demo_data(db: DbSession) -> None:
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
    if not db.scalar(select(NewsSource).where(NewsSource.id == "SRC-GDELT")):
        db.add(NewsSource(
            id="SRC-GDELT",
            name="GDELT Global News",
            url="https://api.gdeltproject.org/api/v2/doc/doc?query=conflict%20OR%20security&mode=artlist&maxrecords=25&format=rss&sort=datedesc",
            source_type="rss",
            description="Free public global news and event feed for synthetic demonstration and analyst review.",
            enabled=True,
            public_access=True,
        ))
    db.commit()
