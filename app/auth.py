"""Operator accounts, sessions and role checks."""
import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request

from . import db

ROLE_LEVEL = {"viewer": 1, "operator": 2, "admin": 3}
SESSION_HOURS = 12


def hash_password(password: str, salt: str = None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000).hex()
    return digest, salt


def verify_password(password: str, digest: str, salt: str) -> bool:
    return secrets.compare_digest(hash_password(password, salt)[0], digest)


def create_user(username, password, role="operator", full_name=""):
    digest, salt = hash_password(password)
    return db.insert("users", {"username": username, "password_hash": digest, "salt": salt,
                               "full_name": full_name, "role": role, "enabled": 1,
                               "created_at": db.now_iso()})


def ensure_default_admin():
    if not db.one("SELECT id FROM users LIMIT 1"):
        create_user("admin", "admin1234", "admin", "System Administrator")


def login(username: str, password: str):
    u = db.one("SELECT * FROM users WHERE username=?", (username,))
    if not u or not u["enabled"] or not verify_password(password, u["password_hash"], u["salt"]):
        return None
    token = secrets.token_urlsafe(32)
    now = datetime.now()
    db.insert("sessions", {"token": token, "user_id": u["id"], "created_at": db.now_iso(),
                           "expires_at": (now + timedelta(hours=SESSION_HOURS)).strftime("%Y-%m-%d %H:%M:%S")})
    db.update("users", u["id"], {"last_login": db.now_iso()})
    return token, public_user(u)


def public_user(u: dict) -> dict:
    return {k: u[k] for k in ("id", "username", "full_name", "role", "enabled", "created_at", "last_login")}


def user_from_token(token: str):
    if not token:
        return None
    row = db.one("SELECT u.*, s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?", (token,))
    if not row or not row["enabled"] or row["expires_at"] < db.now_iso():
        return None
    return public_user(row)


def token_from_request(request: Request) -> str:
    h = request.headers.get("authorization", "")
    if h.lower().startswith("bearer "):
        return h[7:]
    return request.query_params.get("token", "")


def current_user(request: Request) -> dict:
    u = user_from_token(token_from_request(request))
    if not u:
        raise HTTPException(401, "กรุณาเข้าสู่ระบบ")
    return u


def require(role: str):
    def dep(user: dict = Depends(current_user)):
        if ROLE_LEVEL.get(user["role"], 0) < ROLE_LEVEL[role]:
            raise HTTPException(403, "สิทธิ์ไม่เพียงพอสำหรับการดำเนินการนี้")
        return user
    return dep
