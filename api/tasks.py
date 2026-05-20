"""
GET    /api/tasks?tenant=<id>&t=<token>           → lista tareas
POST   /api/tasks?tenant=<id>&t=<token>  body: {client, assignee?, title?, notes?, urgent?, pending_count?}
PATCH  /api/tasks?tenant=<id>&t=<token>  body: {id, ...campos a actualizar}
DELETE /api/tasks?tenant=<id>&t=<token>&id=<task_id>

Campos soportados de una task:
  - id, client, title, assignee, status (pending|done)
  - notes (texto libre)
  - urgent (bool — marca con 🚨)
  - pending_count (int — cuántos archivos/videos quedan pendientes)
  - created_at, completed_at
"""

import datetime
import hashlib
import hmac
import os
import secrets as _secrets
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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        tasks = kv_get(f"tenant:{tenant_id}:tasks") or []
        return json_response(self, {"tasks": tasks})

    def do_POST(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        try:
            data = read_json_body(self)
        except Exception:
            return json_response(self, {"error": "JSON inválido"}, 400)

        client = (data.get("client") or "").strip()
        title = (data.get("title") or "").strip()
        assignee = (data.get("assignee") or "").strip()

        if not client and not title:
            return json_response(self, {"error": "Falta cliente o título"}, 400)

        tasks = kv_get(f"tenant:{tenant_id}:tasks") or []
        new_task = {
            "id": _secrets.token_urlsafe(6),
            "client": client,
            "title": title or client,
            "assignee": assignee,
            "notes": (data.get("notes") or "").strip(),
            "urgent": bool(data.get("urgent", False)),
            "pending_count": int(data.get("pending_count", 1)) or 1,
            "status": "pending",
            "created_at": now_iso(),
        }
        tasks.append(new_task)
        kv_set(f"tenant:{tenant_id}:tasks", tasks)
        return json_response(self, {"ok": True, "task": new_task})

    def do_PATCH(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        try:
            data = read_json_body(self)
        except Exception:
            return json_response(self, {"error": "JSON inválido"}, 400)

        tid = data.get("id")
        if not tid:
            return json_response(self, {"error": "Falta id"}, 400)

        tasks = kv_get(f"tenant:{tenant_id}:tasks") or []
        updated = None
        for t in tasks:
            if t.get("id") == tid:
                # Campos string
                for field in ("client", "title", "assignee", "notes", "status"):
                    if field in data:
                        v = data[field]
                        t[field] = v.strip() if isinstance(v, str) else v
                # urgent (bool)
                if "urgent" in data:
                    t["urgent"] = bool(data["urgent"])
                # pending_count (int)
                if "pending_count" in data:
                    try:
                        t["pending_count"] = max(0, int(data["pending_count"]))
                    except Exception:
                        pass
                # Si status pasa a done y no tenía completed_at
                if t.get("status") == "done" and not t.get("completed_at"):
                    t["completed_at"] = now_iso()
                elif t.get("status") == "pending":
                    t.pop("completed_at", None)
                updated = t
                break
        if not updated:
            return json_response(self, {"error": "Task no encontrada"}, 404)
        kv_set(f"tenant:{tenant_id}:tasks", tasks)
        return json_response(self, {"ok": True, "task": updated})

    def do_DELETE(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        qs = parse_qs(urlparse(self.path).query)
        tid = (qs.get("id", [""])[0]).strip()
        if not tid:
            return json_response(self, {"error": "Falta id"}, 400)
        tasks = kv_get(f"tenant:{tenant_id}:tasks") or []
        new_tasks = [t for t in tasks if t.get("id") != tid]
        if len(new_tasks) == len(tasks):
            return json_response(self, {"error": "Task no encontrada"}, 404)
        kv_set(f"tenant:{tenant_id}:tasks", new_tasks)
        return json_response(self, {"ok": True})
