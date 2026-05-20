"""
POST /api/track
Body: { event, ...meta }

Tracking simple — guarda eventos en KV con TTL corto para analítica básica.
Eventos típicos:
  - pageview            (con .path)
  - waitlist_signup
  - wizard_start
  - wizard_oauth_click
  - oauth_completed

Diseñado para ser ligero: NO usa cookies, NO trackea individuos, solo counts.
"""

import json
import datetime
from http.server import BaseHTTPRequestHandler

from _shared import kv_set, kv_get, _kv_request, json_response, read_json_body


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        try:
            data = read_json_body(self)
        except Exception:
            return json_response(self, {"error": "JSON inválido"}, 400)

        event = (data.get("event") or "").strip()[:50]
        if not event:
            return json_response(self, {"error": "Falta event"}, 400)

        # Increment counter by event + day
        today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
        key = f"track:{today}:{event}"

        try:
            _kv_request(["INCR", key])
            # TTL: 90 días
            _kv_request(["EXPIRE", key, str(90 * 24 * 3600)])
        except Exception:
            pass

        return json_response(self, {"ok": True})
