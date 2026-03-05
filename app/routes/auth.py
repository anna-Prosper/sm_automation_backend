"""
Basic authentication for the dashboard.
Simple token-based auth — not OAuth, just enough to prevent unauthorized access.
"""

import os
import hashlib
import secrets
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])
security = HTTPBearer(auto_error=False)

# In-memory token store (survives until restart; fine for small team)
_active_tokens: dict = {}  # token -> {user, expires_at}

# Default credentials from env (override in .env)
ADMIN_USERNAME = os.getenv("DASHBOARD_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "binayah2026")
TOKEN_EXPIRY_HOURS = int(os.getenv("AUTH_TOKEN_HOURS", "72"))


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_at: str
    user: str


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _create_token(user: str) -> tuple:
    token = secrets.token_urlsafe(48)
    expires = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRY_HOURS)
    _active_tokens[token] = {"user": user, "expires_at": expires}
    return token, expires


async def require_auth(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    """Dependency — attach to any route that needs auth."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Missing authorization token")

    token = credentials.credentials
    session = _active_tokens.get(token)

    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    if datetime.utcnow() > session["expires_at"]:
        _active_tokens.pop(token, None)
        raise HTTPException(status_code=401, detail="Token expired")

    return session


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    if body.username == ADMIN_USERNAME and body.password == ADMIN_PASSWORD:
        token, expires = _create_token(body.username)
        logger.info(f"User '{body.username}' logged in")
        return LoginResponse(token=token, expires_at=expires.isoformat(), user=body.username)
    raise HTTPException(status_code=401, detail="Invalid credentials")


@router.post("/logout")
async def logout(session=Depends(require_auth)):
    # Remove all tokens for this user
    to_remove = [t for t, s in _active_tokens.items() if s["user"] == session["user"]]
    for t in to_remove:
        _active_tokens.pop(t, None)
    return {"ok": True}


@router.get("/me")
async def me(session=Depends(require_auth)):
    return {"user": session["user"], "expires_at": session["expires_at"].isoformat()}
