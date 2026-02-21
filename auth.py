"""
Simple session-based auth using signed cookies.
No external OAuth — just email + bcrypt password.
"""
import hashlib, hmac, os, json, base64
from typing import Optional
from fastapi import Request, HTTPException
from sqlmodel import Session, select
from models import User

SECRET = os.environ.get("GLINT_SECRET", "change-me-in-production-please")


def _hash_password(pw: str) -> str:
    import hashlib
    salt = os.urandom(16).hex()
    h = hashlib.sha256(f"{salt}{pw}{SECRET}".encode()).hexdigest()
    return f"{salt}:{h}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        salt, h = stored.split(":", 1)
        expected = hashlib.sha256(f"{salt}{pw}{SECRET}".encode()).hexdigest()
        return hmac.compare_digest(h, expected)
    except Exception:
        return False


def hash_password(pw: str) -> str:
    return _hash_password(pw)


def create_session_token(user_id: int) -> str:
    payload = base64.b64encode(json.dumps({"uid": user_id}).encode()).decode()
    sig = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def decode_session_token(token: str) -> Optional[int]:
    try:
        payload, sig = token.rsplit(".", 1)
        expected = hmac.new(SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(base64.b64decode(payload))
        return data.get("uid")
    except Exception:
        return None


def get_current_user(request: Request, session: Session) -> Optional[User]:
    token = request.cookies.get("cardifol_session")
    if not token:
        return None
    uid = decode_session_token(token)
    if not uid:
        return None
    return session.get(User, uid)


def require_user(request: Request, session: Session) -> User:
    user = get_current_user(request, session)
    if not user:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user
