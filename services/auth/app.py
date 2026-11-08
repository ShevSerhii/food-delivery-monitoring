"""Сервер авторизації.

nginx перед кожним запитом до Grafana/Prometheus звертається до /verify
(модуль auth_request). Якщо кука сесії валідна - повертаємо 200 і ім'я
користувача в заголовку, інакше 401 і nginx перекидає на сторінку входу.
"""

import os
import time
from pathlib import Path
from string import Template

import jwt
from fastapi import FastAPI, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

import storage

SECRET = os.getenv("AUTH_SECRET", "change-me-please")
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "8"))
COOKIE = "fd_session"
GRAFANA_ROLES = {"admin": "Admin", "viewer": "Viewer"}

login_attempts = Counter("fd_auth_logins_total", "Спроби входу", ["result"])
verify_requests = Counter("fd_auth_verify_total", "Перевірки сесії від nginx", ["result"])

LOGIN_PAGE = Template((Path(__file__).parent / "login.html").read_text(encoding="utf-8"))

app = FastAPI(title="Food Delivery Auth", docs_url=None, redoc_url=None)


@app.on_event("startup")
def startup():
    storage.init_db()


def make_token(username: str, role: str) -> str:
    now = int(time.time())
    payload = {"sub": username, "role": role, "iat": now, "exp": now + SESSION_HOURS * 3600}
    return jwt.encode(payload, SECRET, algorithm="HS256")


def read_token(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    try:
        claims = jwt.decode(token, SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    # користувача могли видалити, поки сесія ще жива
    return claims if storage.user_exists(claims["sub"]) else None


@app.get("/auth/login", response_class=HTMLResponse)
def login_page(error: str = ""):
    message = "Невірний логін або пароль" if error else ""
    return LOGIN_PAGE.substitute(error=message)


@app.post("/auth/login")
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    ip = request.headers.get("x-real-ip") or (request.client.host if request.client else None)
    user = storage.authenticate(username.strip(), password, ip)
    if user is None:
        login_attempts.labels("fail").inc()
        return RedirectResponse("/auth/login?error=1", status_code=303)

    login_attempts.labels("success").inc()
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        COOKIE, make_token(user["username"], user["role"]),
        max_age=SESSION_HOURS * 3600, httponly=True, samesite="lax", path="/",
    )
    return response


@app.get("/auth/logout")
def logout():
    response = RedirectResponse("/auth/login", status_code=303)
    response.delete_cookie(COOKIE, path="/")
    # Grafana тримає власну сесійну куку, її теж прибираємо
    response.delete_cookie("grafana_session", path="/")
    return response


@app.get("/verify")
def verify(request: Request):
    claims = read_token(request)
    if claims is None:
        verify_requests.labels("denied").inc()
        return Response(status_code=401)
    verify_requests.labels("allowed").inc()
    return Response(
        status_code=200,
        headers={"X-User": claims["sub"], "X-User-Role": GRAFANA_ROLES.get(claims["role"], "Viewer")},
    )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
