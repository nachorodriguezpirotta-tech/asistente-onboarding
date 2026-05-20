"""
GET    /api/folder_assignments?tenant=<id>&t=<token>           → lista
POST   /api/folder_assignments?tenant=<id>&t=<token>  body: {folder_id, folder_name, editor_id, editor_name}  → asignar
DELETE /api/folder_assignments?tenant=<id>&t=<token>&folder_id=<id>  → desasignar

Cada tenant tiene un mapeo `folder_id → editor` que define qué editor recibe
las tareas automáticas cuando aparecen archivos nuevos en esa carpeta de Drive.
"""

import hashlib
import hmac
import os
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
        assignments = kv_get(f"tenant:{tenant_id}:folder_assignments") or []
        return json_response(self, {"assignments": assignments})

    def do_POST(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        try:
            data = read_json_body(self)
        except Exception:
            return json_response(self, {"error": "JSON inválido"}, 400)

        folder_id = (data.get("folder_id") or "").strip()
        folder_name = (data.get("folder_name") or "").strip()
        editor_id = (data.get("editor_id") or "").strip()
        editor_name = (data.get("editor_name") or "").strip()

        if not folder_id or not editor_id:
            return json_response(self, {"error": "Falta folder_id o editor_id"}, 400)

        assignments = kv_get(f"tenant:{tenant_id}:folder_assignments") or []
        # Remover el assignment existente de este folder (si lo hay) — solo 1 editor por carpeta
        assignments = [a for a in assignments if a.get("folder_id") != folder_id]
        assignments.append({
            "folder_id": folder_id,
            "folder_name": folder_name,
            "editor_id": editor_id,
            "editor_name": editor_name,
        })
        kv_set(f"tenant:{tenant_id}:folder_assignments", assignments)
        return json_response(self, {"ok": True, "assignments": assignments})

    def do_DELETE(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        qs = parse_qs(urlparse(self.path).query)
        folder_id = (qs.get("folder_id", [""])[0]).strip()
        if not folder_id:
            return json_response(self, {"error": "Falta folder_id"}, 400)
        assignments = kv_get(f"tenant:{tenant_id}:folder_assignments") or []
        new = [a for a in assignments if a.get("folder_id") != folder_id]
        kv_set(f"tenant:{tenant_id}:folder_assignments", new)
        return json_response(self, {"ok": True})
