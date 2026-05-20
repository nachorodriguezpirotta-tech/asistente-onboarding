"""
GET    /api/aliases?tenant=<id>&t=<token>           → lista
POST   /api/aliases?tenant=<id>&t=<token>  body: {nickname, real_name}  → agregar
DELETE /api/aliases?tenant=<id>&t=<token>&id=<alias_id>  → borrar

Apodos: cuando el cliente sube algo a una carpeta llamada con un apodo
(ej. "Roger" en vez de "Roger Martí"), el sistema sabe el nombre real.
"""

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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        aliases = kv_get(f"tenant:{tenant_id}:aliases") or []
        return json_response(self, {"aliases": aliases})

    def do_POST(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        try:
            data = read_json_body(self)
        except Exception:
            return json_response(self, {"error": "JSON inválido"}, 400)
        nick = (data.get("nickname") or "").strip()
        real = (data.get("real_name") or "").strip()
        if not nick or not real:
            return json_response(self, {"error": "Falta nickname o real_name"}, 400)
        aliases = kv_get(f"tenant:{tenant_id}:aliases") or []
        aliases.append({
            "id": _secrets.token_urlsafe(6),
            "nickname": nick.lower(),
            "real_name": real,
        })
        kv_set(f"tenant:{tenant_id}:aliases", aliases)
        return json_response(self, {"ok": True, "aliases": aliases})

    def do_DELETE(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        qs = parse_qs(urlparse(self.path).query)
        aid = (qs.get("id", [""])[0]).strip()
        if not aid:
            return json_response(self, {"error": "Falta id"}, 400)
        aliases = kv_get(f"tenant:{tenant_id}:aliases") or []
        new = [a for a in aliases if a.get("id") != aid]
        kv_set(f"tenant:{tenant_id}:aliases", new)
        return json_response(self, {"ok": True})
