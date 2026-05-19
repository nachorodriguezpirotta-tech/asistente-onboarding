"""
GET /api/admin_get?id=<pedido_id>&t=<admin_token>

Devuelve TODOS los datos del pedido incluyendo tokens OAuth. Solo para vos —
el provision.py local consume este endpoint.

Protegido con ADMIN_TOKEN (env var, setear en Vercel).
"""

import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, kv_update, json_response


ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        pedido_id = (qs.get("id", [""])[0]).strip()
        token = (qs.get("t", [""])[0]).strip()

        if not ADMIN_TOKEN:
            return json_response(self, {"error": "ADMIN_TOKEN no seteado en server"}, 500)
        if token != ADMIN_TOKEN:
            return json_response(self, {"error": "Token inválido"}, 403)
        if not pedido_id:
            return json_response(self, {"error": "Falta id"}, 400)

        pedido = kv_get(f"pedido:{pedido_id}")
        if not pedido:
            return json_response(self, {"error": "Pedido no encontrado"}, 404)

        # Marcar como "leído por provision" (audit trail)
        if pedido.get("status") == "oauth_done":
            kv_update(f"pedido:{pedido_id}", {"status": "provisioning"})

        return json_response(self, pedido)


    def do_POST(self):
        """POST /api/admin_get para actualizar status: { id, t, status, dashboard_url? }"""
        import json
        qs = parse_qs(urlparse(self.path).query)
        token = (qs.get("t", [""])[0]).strip()
        if token != ADMIN_TOKEN:
            return json_response(self, {"error": "Token inválido"}, 403)

        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        pedido_id = body.get("id", "")
        if not pedido_id:
            return json_response(self, {"error": "Falta id"}, 400)

        updates = {k: v for k, v in body.items() if k in ("status", "dashboard_url", "deployed_at")}
        pedido = kv_update(f"pedido:{pedido_id}", updates)
        return json_response(self, pedido or {})
