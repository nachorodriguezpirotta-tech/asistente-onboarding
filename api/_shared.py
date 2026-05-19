"""
Helpers compartidos para los endpoints de onboarding.

Storage: Vercel KV (Redis) — free tier.
Las claves del KV las setea Vercel auto cuando conectás KV al proyecto:
  KV_REST_API_URL, KV_REST_API_TOKEN
"""

import json
import os
import secrets
import urllib.parse
import urllib.request
from typing import Optional


# ─── Vercel KV (Redis REST API) ─────────────────────────────────────────────

KV_URL = os.environ.get("KV_REST_API_URL", "")
KV_TOKEN = os.environ.get("KV_REST_API_TOKEN", "")


def _kv_request(command_list: list) -> dict:
    """Manda comando a Vercel KV via REST. Ej: ['SET', 'key', 'value']"""
    if not KV_URL or not KV_TOKEN:
        raise RuntimeError("Vercel KV no configurado (faltan KV_REST_API_URL / KV_REST_API_TOKEN)")
    body = json.dumps(command_list).encode()
    req = urllib.request.Request(KV_URL, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {KV_TOKEN}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def kv_set(key: str, value: dict, ttl_seconds: int = 7 * 24 * 3600):
    """Guarda dict como JSON. TTL por defecto 7 días."""
    payload = json.dumps(value)
    _kv_request(["SET", key, payload, "EX", str(ttl_seconds)])


def kv_get(key: str) -> Optional[dict]:
    try:
        r = _kv_request(["GET", key])
        raw = r.get("result")
        if raw is None:
            return None
        return json.loads(raw)
    except Exception:
        return None


def kv_update(key: str, updates: dict) -> Optional[dict]:
    """Lee, mergea con updates, escribe. Devuelve el doc actualizado."""
    current = kv_get(key) or {}
    current.update(updates)
    kv_set(key, current)
    return current


# ─── Helpers generales ──────────────────────────────────────────────────────

def new_pedido_id() -> str:
    """ID corto y URL-safe, 12 chars."""
    return secrets.token_urlsafe(9)[:12]


def base_url(handler) -> str:
    """Reconstruye la URL base del request (https://tuapp.vercel.app)."""
    host = handler.headers.get("Host", "localhost:3000")
    proto = handler.headers.get("X-Forwarded-Proto", "https")
    return f"{proto}://{host}"


def json_response(handler, data: dict, status: int = 200):
    body = json.dumps(data).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def redirect(handler, url: str, status: int = 302):
    handler.send_response(status)
    handler.send_header("Location", url)
    handler.send_header("Content-Length", "0")
    handler.end_headers()


def read_json_body(handler) -> dict:
    length = int(handler.headers.get("Content-Length", "0"))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


# ─── Google OAuth ────────────────────────────────────────────────────────────

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "openid", "email", "profile",
]


def google_oauth_url(state: str, redirect_uri: str) -> str:
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",       # necesario para refresh_token
        "prompt": "consent",            # forzar el "Permitir" para que SIEMPRE devuelva refresh_token
        "state": state,
        "include_granted_scopes": "true",
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)


def google_exchange_code(code: str, redirect_uri: str) -> dict:
    """Intercambia el code por tokens. Devuelve dict con access_token, refresh_token, etc."""
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def google_userinfo(access_token: str) -> dict:
    """Obtiene mail del usuario autenticado (para verificación)."""
    req = urllib.request.Request("https://www.googleapis.com/oauth2/v2/userinfo")
    req.add_header("Authorization", f"Bearer {access_token}")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


# ─── Notificación al admin (vos) ─────────────────────────────────────────────

ADMIN_NOTIFY_EMAIL = os.environ.get("ADMIN_NOTIFY_EMAIL", "")
NOTIFY_MAIL_REFRESH_TOKEN = os.environ.get("NOTIFY_MAIL_REFRESH_TOKEN", "")
NOTIFY_MAIL_CLIENT_ID = os.environ.get("NOTIFY_MAIL_CLIENT_ID", GOOGLE_CLIENT_ID)
NOTIFY_MAIL_CLIENT_SECRET = os.environ.get("NOTIFY_MAIL_CLIENT_SECRET", GOOGLE_CLIENT_SECRET)


def _notify_access_token() -> str:
    """Refresca un access_token a partir del refresh_token del admin."""
    data = urllib.parse.urlencode({
        "client_id": NOTIFY_MAIL_CLIENT_ID,
        "client_secret": NOTIFY_MAIL_CLIENT_SECRET,
        "refresh_token": NOTIFY_MAIL_REFRESH_TOKEN,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())["access_token"]


def notify_admin(subject: str, body_text: str, body_html: Optional[str] = None):
    """Manda mail al admin (vos). Silently no-op si no está configurado."""
    if not ADMIN_NOTIFY_EMAIL or not NOTIFY_MAIL_REFRESH_TOKEN:
        return False
    import base64
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    msg = MIMEMultipart("alternative")
    msg["To"] = ADMIN_NOTIFY_EMAIL
    msg["Subject"] = subject
    msg["From"] = "Asistente Onboarding"
    msg.attach(MIMEText(body_text, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

    try:
        access_token = _notify_access_token()
        req = urllib.request.Request(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            data=json.dumps({"raw": raw}).encode(),
            method="POST",
        )
        req.add_header("Authorization", f"Bearer {access_token}")
        req.add_header("Content-Type", "application/json")
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"notify_admin failed: {e}")
        return False
