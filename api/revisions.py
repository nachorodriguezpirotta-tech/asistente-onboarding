"""
GET    /api/revisions?tenant=<id>&t=<token>&status=<open|resolved|all>  → lista revisiones
PATCH  /api/revisions?tenant=<id>&t=<token>  body: {id, status?, resolution_note?}
DELETE /api/revisions?tenant=<id>&t=<token>&id=<rev_id>

Revisiones = pedidos de cambio enviados por clientes.
Storage: KV key `tenant:{tenant_id}:revisions` es una lista de:
  {id, task_id, client_name, message, status, created_at, resolved_at, resolution_note}

status: 'open' | 'resolved'

(La creación de revisiones se hace desde client_portal.py, no acá.)
"""

import datetime
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


def now_iso():
    return datetime.datetime.utcnow().isoformat() + "Z"


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
        self.send_header("Access-Control-Allow-Methods", "GET, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        qs = parse_qs(urlparse(self.path).query)
        status_filter = (qs.get("status", ["all"])[0]).strip().lower() or "all"
        revisions = kv_get(f"tenant:{tenant_id}:revisions") or []
        if status_filter in ("open", "resolved"):
            revisions = [r for r in revisions if r.get("status") == status_filter]
        return json_response(self, {"revisions": revisions})

    def do_PATCH(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        try:
            data = read_json_body(self)
        except Exception:
            return json_response(self, {"error": "JSON inválido"}, 400)

        rid = data.get("id")
        if not rid:
            return json_response(self, {"error": "Falta id"}, 400)

        revisions = kv_get(f"tenant:{tenant_id}:revisions") or []
        updated = None
        for r in revisions:
            if r.get("id") == rid:
                # Campos string
                for field in ("status", "resolution_note"):
                    if field in data:
                        v = data[field]
                        r[field] = v.strip() if isinstance(v, str) else v
                # Si status pasa a resolved y no tenía resolved_at
                if r.get("status") == "resolved" and not r.get("resolved_at"):
                    r["resolved_at"] = now_iso()
                elif r.get("status") == "open":
                    r.pop("resolved_at", None)
                updated = r
                break
        if not updated:
            return json_response(self, {"error": "Revisión no encontrada"}, 404)
        kv_set(f"tenant:{tenant_id}:revisions", revisions)
        return json_response(self, {"ok": True, "revision": updated})

    def do_DELETE(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        qs = parse_qs(urlparse(self.path).query)
        rid = (qs.get("id", [""])[0]).strip()
        if not rid:
            return json_response(self, {"error": "Falta id"}, 400)
        revisions = kv_get(f"tenant:{tenant_id}:revisions") or []
        new_revisions = [r for r in revisions if r.get("id") != rid]
        if len(new_revisions) == len(revisions):
            return json_response(self, {"error": "Revisión no encontrada"}, 404)
        kv_set(f"tenant:{tenant_id}:revisions", new_revisions)
        return json_response(self, {"ok": True})
