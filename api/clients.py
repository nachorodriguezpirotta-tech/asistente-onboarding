"""
GET    /api/clients?tenant=<id>&t=<token>           → lista clientes
POST   /api/clients?tenant=<id>&t=<token>  body: {name, email?, phone?, notes?}
PATCH  /api/clients?tenant=<id>&t=<token>  body: {id, ...campos a actualizar}
DELETE /api/clients?tenant=<id>&t=<token>&id=<client_id>

Campos de un cliente:
  - id, name, email, phone, notes
  - portal_token (token URL-safe que el cliente usa para acceder a su portal)
  - created_at
"""

import datetime
import hashlib
import hmac
import os
import secrets as _secrets
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, kv_set, json_response, read_json_body


SECRET = os.environ.get("DASHBOARD_SECRET", "CHANGE_ME")


def verify_token(tenant_id, token):
    expected = hmac.new(SECRET.encode(), tenant_id.encode(), hashlib.sha256).hexdigest()[:24]
    return hmac.compare_digest(expected, token or "")


def now_iso():
    return datetime.datetime.utcnow().isoformat() + "Z"


class handler(BaseHTTPRequestHandler):
    def _auth(self):
        qs = parse_qs(urlparse(self.path).query)
        tenant_id = (qs.get("tenant", [""])[0]).strip()
        token = (qs.get("t", [""])[0]).strip()
        if not tenant_id or not verify_token(tenant_id, token):
            return None
        return tenant_id

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        clients = kv_get(f"tenant:{tenant_id}:clients") or []
        return json_response(self, {"clients": clients})

    def do_POST(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        try:
            data = read_json_body(self)
        except Exception:
            return json_response(self, {"error": "JSON inválido"}, 400)

        name = (data.get("name") or "").strip()
        if not name:
            return json_response(self, {"error": "Falta nombre"}, 400)

        email = (data.get("email") or "").strip()
        phone = (data.get("phone") or "").strip()
        notes = (data.get("notes") or "").strip()

        clients = kv_get(f"tenant:{tenant_id}:clients") or []
        new_client = {
            "id": _secrets.token_urlsafe(6),
            "name": name,
            "email": email,
            "phone": phone,
            "notes": notes,
            "portal_token": _secrets.token_urlsafe(16),
            "created_at": now_iso(),
        }
        clients.append(new_client)
        kv_set(f"tenant:{tenant_id}:clients", clients)
        return json_response(self, {"ok": True, "client": new_client})

    def do_PATCH(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        try:
            data = read_json_body(self)
        except Exception:
            return json_response(self, {"error": "JSON inválido"}, 400)

        cid = data.get("id")
        if not cid:
            return json_response(self, {"error": "Falta id"}, 400)

        clients = kv_get(f"tenant:{tenant_id}:clients") or []
        updated = None
        for c in clients:
            if c.get("id") == cid:
                for field in ("name", "email", "phone", "notes"):
                    if field in data:
                        v = data[field]
                        c[field] = v.strip() if isinstance(v, str) else v
                updated = c
                break
        if not updated:
            return json_response(self, {"error": "Cliente no encontrado"}, 404)
        kv_set(f"tenant:{tenant_id}:clients", clients)
        return json_response(self, {"ok": True, "client": updated})

    def do_DELETE(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        qs = parse_qs(urlparse(self.path).query)
        cid = (qs.get("id", [""])[0]).strip()
        if not cid:
            return json_response(self, {"error": "Falta id"}, 400)
        clients = kv_get(f"tenant:{tenant_id}:clients") or []
        new_clients = [c for c in clients if c.get("id") != cid]
        if len(new_clients) == len(clients):
            return json_response(self, {"error": "Cliente no encontrado"}, 404)
        kv_set(f"tenant:{tenant_id}:clients", new_clients)
        return json_response(self, {"ok": True})
