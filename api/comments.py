"""
GET    /api/comments?tenant=<id>&t=<token>&task_id=<optional>&client=<optional>
       → devuelve lista filtrada de comments
POST   /api/comments?tenant=<id>&t=<token>  body: {task_id?, client?, author, text}
       → crea comment con id nuevo, registra en timeline
DELETE /api/comments?tenant=<id>&t=<token>&id=<comment_id>
       → elimina el comment

Storage: KV key `tenant:{tenant_id}:comments` es una lista de
{id, task_id (optional), client (optional), author, text, created_at}.
"""

import datetime
import hashlib
import hmac
import os
import secrets as _secrets
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, kv_set, json_response, read_json_body, timeline_add


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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)

        qs = parse_qs(urlparse(self.path).query)
        task_id = (qs.get("task_id", [""])[0]).strip()
        client_filter = (qs.get("client", [""])[0]).strip().lower()

        comments = kv_get(f"tenant:{tenant_id}:comments") or []

        if task_id:
            comments = [c for c in comments if c.get("task_id") == task_id]
        if client_filter:
            comments = [
                c for c in comments
                if client_filter in (c.get("client") or "").lower()
            ]

        return json_response(self, {"comments": comments})

    def do_POST(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        try:
            data = read_json_body(self)
        except Exception:
            return json_response(self, {"error": "JSON inválido"}, 400)

        task_id = (data.get("task_id") or "").strip()
        client = (data.get("client") or "").strip()
        author = (data.get("author") or "").strip()
        text = (data.get("text") or "").strip()

        if not text:
            return json_response(self, {"error": "Falta text"}, 400)
        if not author:
            return json_response(self, {"error": "Falta author"}, 400)

        comments = kv_get(f"tenant:{tenant_id}:comments") or []
        new_comment = {
            "id": _secrets.token_urlsafe(6),
            "task_id": task_id,
            "client": client,
            "author": author,
            "text": text,
            "created_at": now_iso(),
        }
        comments.append(new_comment)
        kv_set(f"tenant:{tenant_id}:comments", comments)

        try:
            timeline_add(
                tenant_id,
                "comment_added",
                client=client,
                actor=author,
                payload={"task_id": task_id, "text_preview": text[:80]},
            )
        except Exception:
            pass

        return json_response(self, {"ok": True, "comment": new_comment})

    def do_DELETE(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        qs = parse_qs(urlparse(self.path).query)
        cid = (qs.get("id", [""])[0]).strip()
        if not cid:
            return json_response(self, {"error": "Falta id"}, 400)
        comments = kv_get(f"tenant:{tenant_id}:comments") or []
        new_comments = [c for c in comments if c.get("id") != cid]
        if len(new_comments) == len(comments):
            return json_response(self, {"error": "Comment no encontrado"}, 404)
        kv_set(f"tenant:{tenant_id}:comments", new_comments)
        return json_response(self, {"ok": True})
