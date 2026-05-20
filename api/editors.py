"""
GET    /api/editors?tenant=<id>&t=<token>          → lista
POST   /api/editors?tenant=<id>&t=<token>  body: {name, email}  → agregar
PATCH  /api/editors?tenant=<id>&t=<token>  body: {id, name?, email?}  → editar
DELETE /api/editors?tenant=<id>&t=<token>&id=<editor_id>  → borrar
"""

import hashlib
import hmac
import json
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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        editors = kv_get(f"tenant:{tenant_id}:editors") or []
        return json_response(self, {"editors": editors})

    def do_POST(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        try:
            data = read_json_body(self)
        except Exception:
            return json_response(self, {"error": "JSON inválido"}, 400)

        name = (data.get("name") or "").strip()
        email = (data.get("email") or "").strip().lower()
        if not name:
            return json_response(self, {"error": "Falta name"}, 400)

        editors = kv_get(f"tenant:{tenant_id}:editors") or []
        if any(e["name"].lower() == name.lower() for e in editors):
            return json_response(self, {"error": "Ya existe un editor con ese nombre"}, 400)

        editor = {
            "id": _secrets.token_urlsafe(6),
            "name": name,
            "email": email,
            "active": True,
        }
        editors.append(editor)
        kv_set(f"tenant:{tenant_id}:editors", editors)
        return json_response(self, {"ok": True, "editor": editor})

    def do_PATCH(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        try:
            data = read_json_body(self)
        except Exception:
            return json_response(self, {"error": "JSON inválido"}, 400)

        eid = data.get("id")
        if not eid:
            return json_response(self, {"error": "Falta id"}, 400)

        editors = kv_get(f"tenant:{tenant_id}:editors") or []
        updated = None
        for e in editors:
            if e.get("id") == eid:
                if "name" in data:
                    e["name"] = data["name"].strip()
                if "email" in data:
                    e["email"] = data["email"].strip().lower()
                if "active" in data:
                    e["active"] = bool(data["active"])
                updated = e
                break
        if not updated:
            return json_response(self, {"error": "Editor no encontrado"}, 404)
        kv_set(f"tenant:{tenant_id}:editors", editors)
        return json_response(self, {"ok": True, "editor": updated})

    def do_DELETE(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        qs = parse_qs(urlparse(self.path).query)
        eid = (qs.get("id", [""])[0]).strip()
        if not eid:
            return json_response(self, {"error": "Falta id"}, 400)
        editors = kv_get(f"tenant:{tenant_id}:editors") or []
        new_editors = [e for e in editors if e.get("id") != eid]
        if len(new_editors) == len(editors):
            return json_response(self, {"error": "Editor no encontrado"}, 404)
        kv_set(f"tenant:{tenant_id}:editors", new_editors)
        return json_response(self, {"ok": True})
