"""
Endpoint PÚBLICO del portal del cliente. NO usa HMAC token del dashboard.
Auth: portal_token del cliente (generado al crearlo en /api/clients).

GET  /api/client_portal?tenant=<tenant_id>&client_token=<portal_token>
     → devuelve branding + cliente + tasks pendientes/done + timeline.

POST /api/client_portal?tenant=<tenant_id>&client_token=<portal_token>
     body: {task_id, message}
     → registra revisión, agrega evento al timeline, marca la task como urgente.
"""

import datetime
import secrets as _secrets
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, kv_set, json_response, read_json_body, timeline_add


def now_iso():
    return datetime.datetime.utcnow().isoformat() + "Z"


def _find_client(tenant_id, client_token):
    """Devuelve el dict del cliente cuyo portal_token == client_token, o None."""
    if not tenant_id or not client_token:
        return None
    clients = kv_get(f"tenant:{tenant_id}:clients") or []
    for c in clients:
        if c.get("portal_token") == client_token:
            return c
    return None


def _name_matches(task_client, client_name):
    """Match fuzzy bidireccional por substring (case-insensitive)."""
    a = (task_client or "").strip().lower()
    b = (client_name or "").strip().lower()
    if not a or not b:
        return False
    return (a in b) or (b in a)


class handler(BaseHTTPRequestHandler):
    def _params(self):
        qs = parse_qs(urlparse(self.path).query)
        tenant_id = (qs.get("tenant", [""])[0]).strip()
        client_token = (qs.get("client_token", [""])[0]).strip()
        return tenant_id, client_token

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        tenant_id, client_token = self._params()
        client = _find_client(tenant_id, client_token)
        if not client:
            return json_response(self, {"error": "Acceso denegado"}, 403)

        client_name = client.get("name", "")

        # Tenant doc → branding
        tenant_doc = kv_get(f"tenant:{tenant_id}") or {}
        brand_name = tenant_doc.get("brand_name") or tenant_doc.get("business_name") or ""
        business_name = tenant_doc.get("business_name") or ""

        # Tasks
        tasks = kv_get(f"tenant:{tenant_id}:tasks") or []
        matched = [t for t in tasks if _name_matches(t.get("client", ""), client_name)]
        tasks_pending = [t for t in matched if t.get("status") != "done"]
        tasks_done = [t for t in matched if t.get("status") == "done"]
        # Últimos 20 done (por completed_at o created_at desc)
        tasks_done.sort(
            key=lambda t: t.get("completed_at") or t.get("created_at") or "",
            reverse=True,
        )
        tasks_done = tasks_done[:20]

        # Timeline
        timeline = kv_get(f"tenant:{tenant_id}:timeline") or []
        timeline_filtered = [e for e in timeline if _name_matches(e.get("client", ""), client_name)]
        timeline_filtered = timeline_filtered[:30]

        return json_response(self, {
            "brand_name": brand_name,
            "business_name": business_name,
            "client": {
                "name": client_name,
                "email": client.get("email", ""),
            },
            "tasks_pending": tasks_pending,
            "tasks_done": tasks_done,
            "timeline": timeline_filtered,
        })

    def do_POST(self):
        tenant_id, client_token = self._params()
        client = _find_client(tenant_id, client_token)
        if not client:
            return json_response(self, {"error": "Acceso denegado"}, 403)

        try:
            data = read_json_body(self)
        except Exception:
            return json_response(self, {"error": "JSON inválido"}, 400)

        task_id = (data.get("task_id") or "").strip()
        message = (data.get("message") or "").strip()
        if not task_id or not message:
            return json_response(self, {"error": "Falta task_id o message"}, 400)

        client_name = client.get("name", "")

        # 1) Guardar revisión
        rev_key = f"tenant:{tenant_id}:revisions"
        revisions = kv_get(rev_key) or []
        revision = {
            "id": _secrets.token_urlsafe(6),
            "task_id": task_id,
            "client_name": client_name,
            "message": message,
            "status": "open",
            "created_at": now_iso(),
        }
        revisions.append(revision)
        kv_set(rev_key, revisions)

        # 2) Timeline
        try:
            timeline_add(
                tenant_id,
                "revision_requested",
                client=client_name,
                actor=client_name,
                payload={"task_id": task_id, "message_preview": message[:80]},
            )
        except Exception:
            pass

        # 3) Marcar task como urgente + append a notes
        tasks = kv_get(f"tenant:{tenant_id}:tasks") or []
        for t in tasks:
            if t.get("id") == task_id:
                t["urgent"] = True
                existing_notes = (t.get("notes") or "").rstrip()
                stamp = now_iso()
                addition = f"[Revisión cliente {client_name} {stamp}] {message}"
                t["notes"] = (existing_notes + "\n\n" + addition).strip() if existing_notes else addition
                break
        kv_set(f"tenant:{tenant_id}:tasks", tasks)

        return json_response(self, {"ok": True})
