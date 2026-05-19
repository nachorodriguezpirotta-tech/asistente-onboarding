"""
GET /api/oauth_init?id=<pedido_id>

Redirige al usuario a la pantalla de OAuth de Google.
"""

from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, base_url, google_oauth_url, redirect, json_response


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        pedido_id = (qs.get("id", [""])[0]).strip()

        if not pedido_id:
            return json_response(self, {"error": "Falta id"}, 400)

        pedido = kv_get(f"pedido:{pedido_id}")
        if not pedido:
            return json_response(self, {"error": "Pedido no encontrado o expirado"}, 404)

        redirect_uri = f"{base_url(self)}/api/oauth_callback"
        url = google_oauth_url(state=pedido_id, redirect_uri=redirect_uri)
        return redirect(self, url)
