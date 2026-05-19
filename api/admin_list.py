"""
GET /api/admin_list?t=<admin_token>

Devuelve la lista de TODOS los pedidos en KV (para el admin panel).
Protegido por ADMIN_TOKEN.
"""

import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, _kv_request, json_response


ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        token = (qs.get("t", [""])[0]).strip()

        if not ADMIN_TOKEN:
            return json_response(self, {"error": "ADMIN_TOKEN no seteado en server"}, 500)
        if token != ADMIN_TOKEN:
            return json_response(self, {"error": "Token inválido"}, 403)

        # Listar todas las keys "pedido:*"
        try:
            keys_r = _kv_request(["KEYS", "pedido:*"])
            keys = keys_r.get("result", []) or []
        except Exception as e:
            return json_response(self, {"error": f"KV: {e}", "pedidos": []}, 500)

        pedidos = []
        for k in keys:
            p = kv_get(k)
            if p:
                # Resumen: NO incluir tokens en la lista
                pedidos.append({
                    "id": p.get("id"),
                    "brand_name": p.get("brand_name"),
                    "admin_email": p.get("admin_email"),
                    "preset": p.get("preset"),
                    "status": p.get("status"),
                    "google_email": p.get("google_email", ""),
                    "drive_url": p.get("drive_url", ""),
                    "created_at": p.get("created_at"),
                    "deployed_at": p.get("deployed_at"),
                    "dashboard_url": p.get("dashboard_url", ""),
                    "has_oauth": bool(p.get("oauth_refresh_token")),
                })

        # Ordenar por created_at descending
        pedidos.sort(key=lambda x: x.get("created_at") or "", reverse=True)

        return json_response(self, {"pedidos": pedidos, "total": len(pedidos)})
