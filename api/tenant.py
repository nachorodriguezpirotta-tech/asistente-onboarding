"""
GET /api/tenant/<tenant_id>?t=<token>
  Devuelve datos completos del tenant (perfil + editores + tareas + clientes).

PATCH /api/tenant/<tenant_id>?t=<token>
  Body: { brand_name?, vocabulary?, ... }
  Actualiza campos del tenant.

Cada tenant tiene un HMAC token único. Si el token no matchea, 403.
"""

import hashlib
import hmac
import json
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, kv_update, kv_set, json_response, read_json_body


SECRET = os.environ.get("DASHBOARD_SECRET", "CHANGE_ME")


def make_tenant_token(tenant_id: str) -> str:
    return hmac.new(SECRET.encode(), tenant_id.encode(), hashlib.sha256).hexdigest()[:24]


def verify_token(tenant_id: str, token: str) -> bool:
    return hmac.compare_digest(make_tenant_token(tenant_id), token or "")


def load_tenant_full(tenant_id: str) -> dict:
    """Carga el tenant + sus colecciones (editores, tareas, clientes)."""
    profile = kv_get(f"tenant:{tenant_id}") or {}
    editors = kv_get(f"tenant:{tenant_id}:editors") or []
    tasks = kv_get(f"tenant:{tenant_id}:tasks") or []
    clients = kv_get(f"tenant:{tenant_id}:clients") or []
    # NO devolvemos tokens OAuth ni secrets al frontend
    safe_profile = {k: v for k, v in profile.items() if not k.startswith("oauth_") and k != "token"}
    return {
        "profile": safe_profile,
        "editors": editors,
        "tasks": tasks,
        "clients": clients,
    }


class handler(BaseHTTPRequestHandler):
    def _get_tenant_id(self):
        path = urlparse(self.path).path
        parts = [p for p in path.split("/") if p]
        # /api/tenant/<id>
        if len(parts) >= 3 and parts[0] == "api" and parts[1] == "tenant":
            return parts[2]
        return None

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        tenant_id = self._get_tenant_id()
        if not tenant_id:
            return json_response(self, {"error": "Falta tenant_id"}, 400)
        qs = parse_qs(urlparse(self.path).query)
        token = (qs.get("t", [""])[0]).strip()
        if not verify_token(tenant_id, token):
            return json_response(self, {"error": "Token inválido"}, 403)

        data = load_tenant_full(tenant_id)
        if not data["profile"]:
            return json_response(self, {"error": "Tenant no encontrado"}, 404)
        return json_response(self, data)

    def do_PATCH(self):
        tenant_id = self._get_tenant_id()
        if not tenant_id:
            return json_response(self, {"error": "Falta tenant_id"}, 400)
        qs = parse_qs(urlparse(self.path).query)
        token = (qs.get("t", [""])[0]).strip()
        if not verify_token(tenant_id, token):
            return json_response(self, {"error": "Token inválido"}, 403)

        try:
            updates = read_json_body(self)
        except Exception:
            return json_response(self, {"error": "JSON inválido"}, 400)

        # Solo permitir editar campos públicos
        allowed = {"brand_name", "admin_email", "preset",
                   "input_singular", "input_plural",
                   "output_singular", "output_plural",
                   "assignee_singular", "assignee_plural",
                   "project_singular", "project_plural",
                   "primary_color", "accent_color",
                   # Preferencias de notificaciones del admin (Rafa)
                   "notify_on_task_done",     # mail al admin cuando editor termina
                   "notify_on_revision",      # mail al admin cuando cliente pide cambio
                   "notify_on_upload"}        # mail al admin cuando cliente sube material
        clean = {k: v for k, v in updates.items() if k in allowed}
        if not clean:
            return json_response(self, {"error": "Nada que actualizar"}, 400)

        result = kv_update(f"tenant:{tenant_id}", clean)
        safe = {k: v for k, v in (result or {}).items() if not k.startswith("oauth_") and k != "token"}
        return json_response(self, {"ok": True, "profile": safe})
