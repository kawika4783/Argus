from datetime import datetime, timedelta, timezone
from secrets import token_urlsafe
from hmac import compare_digest
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

password_hasher = PasswordHasher()

def hash_password(password: str) -> str:
    return password_hasher.hash(password)

def verify_password(password: str, encoded: str) -> bool:
    try:
        return password_hasher.verify(encoded, password)
    except VerifyMismatchError:
        return False

def new_session_id() -> str:
    return token_urlsafe(48)

def secrets_match(provided: str, expected: str) -> bool:
    return bool(expected) and compare_digest(provided, expected)

def expires_in(hours: int) -> datetime:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).replace(tzinfo=None)
