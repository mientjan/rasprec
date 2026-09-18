"""Single-admin authentication. Secrets are runtime inputs, never source defaults."""

import asyncio
import hashlib
import os
import secrets
import time
from collections import deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.security import HTTPBasic

COOKIE = "__Host-rasprec"
TTL = 12 * 3600
basic = HTTPBasic(auto_error=False)
hasher = PasswordHasher()
LOGIN = """<!doctype html><html><meta name="viewport" content="width=device-width">
<title>RaspRec login</title>
<style>
body{font:16px/1.5 system-ui;background:#14161a;color:#e6e9ef;max-width:360px;margin:12vh auto;padding:24px}
form,label{display:grid;gap:12px} form{gap:24px;padding:24px;background:#1d2026;border-radius:12px}
input,button{font:inherit;padding:10px;border-radius:6px;border:1px solid #3b4250}
input{background:#14161a;color:inherit} button{background:#4ea1ff;color:#07121f;cursor:pointer}
#error{color:#ff9595}
</style><body><h1>RaspRec</h1><p>Sign in to your camera recordings.</p>
<form id="login"><label>Username <input id="user" autocomplete="username" required></label>
<label>Password <input id="password" type="password" autocomplete="current-password" required></label>
<button>Sign in</button></form><p id="error" role="alert"></p>
<script>
document.querySelector('#login').onsubmit=async(e)=>{
e.preventDefault();
try {
const r=await fetch('/login',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({username:document.querySelector('#user').value,password:document.querySelector('#password').value})});
if(r.ok) location.assign('/'); else document.querySelector('#error').textContent='Login failed. Check credentials or try again later.';
} catch {document.querySelector('#error').textContent='Connection failed.'}
};
</script></body></html>"""


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class Auth:
    def __init__(self, db):
        self.db = db
        self.mode = os.environ.get("NVR_AUTH_MODE", "basic")
        self.user = os.environ.get("NVR_USER", "admin")
        self.origin = os.environ.get("NVR_PUBLIC_ORIGIN", "").rstrip("/")
        self.password_hash = os.environ.get("NVR_PASSWORD_HASH", "")
        self.attempts = deque()
        self.hash_slots = asyncio.Semaphore(2)
        if self.mode == "session":
            if not self.origin.startswith(
                "https://"
            ) or not self.password_hash.startswith("$argon2id$"):
                raise ValueError(
                    "Session mode needs an HTTPS NVR_PUBLIC_ORIGIN and Argon2id NVR_PASSWORD_HASH"
                )
            from urllib.parse import urlsplit

            parsed = urlsplit(self.origin)
            if (
                not parsed.netloc
                or parsed.path
                or parsed.query
                or parsed.fragment
                or parsed.username
            ):
                raise ValueError(
                    "NVR_PUBLIC_ORIGIN must be an HTTPS origin without path or credentials"
                )
            from argon2 import extract_parameters

            extract_parameters(self.password_hash)
        elif self.mode != "basic" or not os.environ.get("NVR_PASS"):
            raise ValueError(
                "Select session authentication, or explicitly supply a private Basic-auth password"
            )
        with db._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS sessions
                (token TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires REAL NOT NULL)""")

    def same_origin(self, request):
        expected = (
            self.origin if self.mode == "session" else str(request.base_url).rstrip("/")
        )
        if request.headers.get("origin") != expected:
            raise HTTPException(403, "invalid origin")

    async def require(self, request):
        if self.mode == "basic":
            cred = await basic(request)
            if not cred or not (
                secrets.compare_digest(cred.username.encode(), self.user.encode())
                and secrets.compare_digest(
                    cred.password.encode(), os.environ["NVR_PASS"].encode()
                )
            ):
                raise HTTPException(
                    401,
                    "authentication required",
                    headers={"WWW-Authenticate": "Basic"},
                )
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                self.same_origin(request)
                if request.headers.get("x-capture-request") != "1":
                    raise HTTPException(403, "missing request header")
            return self.user
        token = request.cookies.get(COOKIE, "")
        with self.db._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE expires <= ?", (time.time(),))
            row = conn.execute(
                "SELECT * FROM sessions WHERE token=?", (digest(token),)
            ).fetchone()
        if not row:
            raise HTTPException(401, "authentication required")
        request.state.csrf = row["csrf"]
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            self.same_origin(request)
            if not secrets.compare_digest(
                request.headers.get("x-csrf-token", "").encode(), row["csrf"].encode()
            ):
                raise HTTPException(403, "invalid CSRF token")
        return self.user

    async def login(self, request):
        if self.mode != "session":
            raise HTTPException(404, "session login disabled")
        if request.method == "GET":
            return HTMLResponse(LOGIN, headers={"Cache-Control": "no-store"})
        self.same_origin(request)
        now = time.monotonic()
        while self.attempts and self.attempts[0] < now - 60:
            self.attempts.popleft()
        # Global bounded limiter avoids spoofed-forwarded-IP bypass and unbounded maps.
        if len(self.attempts) >= 10:
            raise HTTPException(429, "try again later", headers={"Retry-After": "60"})
        self.attempts.append(now)
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 4096:
                raise HTTPException(413, "request too large")
        try:
            import json

            data = json.loads(body)
            username, password = data["username"], data["password"]
            if not isinstance(username, str) or not isinstance(password, str):
                raise ValueError()
            username.encode("utf-8")
            password.encode("utf-8")
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, "invalid login request") from None
        try:
            async with self.hash_slots:
                valid = await asyncio.to_thread(
                    hasher.verify, self.password_hash, password
                )
        except (VerificationError, InvalidHashError):
            valid = False
        if not valid or not secrets.compare_digest(
            username.encode(), self.user.encode()
        ):
            raise HTTPException(401, "invalid credentials")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.db._connect() as conn:
            conn.execute("DELETE FROM sessions WHERE expires <= ?", (time.time(),))
            conn.execute(
                "INSERT INTO sessions VALUES (?, ?, ?)",
                (digest(token), csrf, time.time() + TTL),
            )
        response = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
        response.set_cookie(
            COOKIE,
            token,
            max_age=TTL,
            secure=True,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return response

    async def logout(self, request):
        await self.require(request)
        with self.db._connect() as conn:
            conn.execute(
                "DELETE FROM sessions WHERE token=?",
                (digest(request.cookies.get(COOKIE, "")),),
            )
        response = JSONResponse({"ok": True})
        response.delete_cookie(
            COOKIE, secure=True, httponly=True, samesite="strict", path="/"
        )
        return response
