"""
GET /api/timeline?tenant=<id>&t=<token>&client=<optional_client_name>&limit=<default_50>
  → devuelve eventos del timeline del tenant, más recientes primero.

Storage: KV key `tenant:{tenant_id}:timeline` es una lista de eventos
{ts, type, client, actor, payload}.
"""

import hashlib
import hmac
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, json_response


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
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)

        qs = parse_qs(urlparse(self.path).query)
        client_filter = (qs.get("client", [""])[0]).strip().lower()
        try:
            limit = int((qs.get("limit", ["50"])[0]).strip())
        except Exception:
            limit = 50
        if limit <= 0:
            limit = 50

        events = kv_get(f"tenant:{tenant_id}:timeline") or []

        if client_filter:
            events = [
                e for e in events
                if client_filter in (e.get("client") or "").lower()
            ]

        events = events[:limit]
        return json_response(self, {"events": events})
