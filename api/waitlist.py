"""
POST /api/waitlist
Body: { email, source? }

Guarda un signup en la waitlist (alguien que dejó su mail desde la landing sin
hacer el flow completo). Vos los ves en /admin con filter waitlist.
"""

import json
import re
import datetime
from http.server import BaseHTTPRequestHandler

from _shared import kv_set, kv_get, json_response, read_json_body


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

        email = (data.get("email") or "").strip().lower()
        source = (data.get("source") or "landing").strip()[:50]

        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            return json_response(self, {"error": "Email inválido"}, 400)

        # Idempotente: si el mail ya está, no duplicar
        key = f"waitlist:{email}"
        existing = kv_get(key)
        if existing:
            return json_response(self, {"ok": True, "already_signed_up": True})

        kv_set(key, {
            "email": email,
            "source": source,
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
        }, ttl_seconds=365 * 24 * 3600)  # 1 año

        return json_response(self, {"ok": True})
