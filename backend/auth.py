import base64
import hashlib
import hmac
import secrets
import threading
from datetime import datetime, timedelta, timezone
from html import escape

from fastapi import HTTPException

from .database import db, now_iso

_captcha_lock = threading.Lock()
_captchas: dict[str, tuple[str, datetime]] = {}
_CAPTCHA_TTL = timedelta(minutes=5)
_SESSION_TTL = timedelta(days=30)
_SESSION_TTL_SHORT = timedelta(hours=12)


def _utcnow():
    return datetime.now(timezone.utc)


def _password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 310_000)
    return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"


def _password_matches(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_hex, digest_hex = encoded.split("$", 2)
        if algorithm != "pbkdf2_sha256":
            return False
        return hmac.compare_digest(_password_hash(password, bytes.fromhex(salt_hex)).split("$", 2)[2], digest_hex)
    except (ValueError, TypeError):
        return False


def create_captcha():
    code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(5))
    captcha_id = secrets.token_urlsafe(24)
    width = 150
    positions = [f"x='{18 + index * 27}' y='{32 + secrets.randbelow(5) - 2}'" for index in range(5)]
    letters = "".join(
        f"<text {position} transform='rotate({secrets.randbelow(13) - 6} {18 + index * 27} 30)'>{escape(char)}</text>"
        for index, (position, char) in enumerate(zip(positions, code))
    )
    svg = (
        f"<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='46' viewBox='0 0 {width} 46'>"
        "<defs><pattern id='captcha-dots' width='10' height='10' patternUnits='userSpaceOnUse'>"
        "<circle cx='2' cy='2' r='.65' fill='#8BA4BF' opacity='.25'/></pattern></defs>"
        "<rect width='150' height='46' rx='8' fill='#F3F6FB'/>"
        "<rect width='150' height='46' rx='8' fill='url(#captcha-dots)'/>"
        f"<g fill='#244E7B' font-family='Consolas, Courier New, monospace' font-size='23' font-weight='600'>{letters}</g></svg>"
    )
    with _captcha_lock:
        now = _utcnow()
        for key, (_, expires) in list(_captchas.items()):
            if expires <= now:
                _captchas.pop(key, None)
        _captchas[captcha_id] = (code, now + _CAPTCHA_TTL)
    image = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return {"captcha_id": captcha_id, "image": f"data:image/svg+xml;base64,{image}"}


def verify_captcha(captcha_id: str, value: str):
    with _captcha_lock:
        entry = _captchas.pop(captcha_id, None)
    if not entry or entry[1] <= _utcnow() or not hmac.compare_digest(entry[0], value.strip().upper()):
        raise HTTPException(status_code=400, detail="验证码错误或已过期，请重试")


def register_user(username: str, email: str, password: str):
    with db() as connection:
        exists = connection.execute(
            "SELECT 1 FROM auth_users WHERE username = ? COLLATE NOCASE OR email = ? COLLATE NOCASE",
            (username, email),
        ).fetchone()
        if exists:
            raise HTTPException(status_code=409, detail="账号或邮箱已存在")
        connection.execute(
            "INSERT INTO auth_users(username,email,password_hash,created_at) VALUES(?,?,?,?)",
            (username, email, _password_hash(password), now_iso()),
        )
    return {"registered": True, "username": username}


def create_session(username: str, password: str, remember: bool):
    with db() as connection:
        row = connection.execute(
            "SELECT username,password_hash FROM auth_users WHERE username = ? COLLATE NOCASE", (username,)
        ).fetchone()
        if not row or not _password_matches(password, row["password_hash"]):
            raise HTTPException(status_code=401, detail="账号或密码错误")
        token = secrets.token_urlsafe(36)
        expires = _utcnow() + (_SESSION_TTL if remember else _SESSION_TTL_SHORT)
        connection.execute(
            "INSERT INTO auth_sessions(token_hash,username,expires_at,created_at) VALUES(?,?,?,?)",
            (hashlib.sha256(token.encode()).hexdigest(), row["username"], expires.isoformat(), now_iso()),
        )
    return {"token": token, "user": {"username": row["username"]}}


def session_username(token: str | None):
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    with db() as connection:
        row = connection.execute(
            "SELECT username,expires_at FROM auth_sessions WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if not row:
            return None
        if datetime.fromisoformat(row["expires_at"]) <= _utcnow():
            connection.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
            return None
        return row["username"]


def require_session(authorization: str | None):
    scheme, _, token = (authorization or "").partition(" ")
    username = session_username(token if scheme.lower() == "bearer" else None)
    if not username:
        raise HTTPException(status_code=401, detail="登录状态已失效")
    return username


def revoke_session(authorization: str | None):
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        return
    with db() as connection:
        connection.execute(
            "DELETE FROM auth_sessions WHERE token_hash = ?",
            (hashlib.sha256(token.encode()).hexdigest(),),
        )
