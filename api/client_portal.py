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

from _shared import kv_get, kv_set, json_response, read_json_body, timeline_add, notify_via_tenant


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

        # 3) Marcar task como urgente + append a notes + sacar el editor asignado
        tasks = kv_get(f"tenant:{tenant_id}:tasks") or []
        editor_name = ""
        task_title = ""
        for t in tasks:
            if t.get("id") == task_id:
                t["urgent"] = True
                # Si la task estaba "done", reabrirla
                if t.get("status") == "done":
                    t["status"] = "pending"
                    t["pending_count"] = max(1, int(t.get("pending_count", 0)))
                existing_notes = (t.get("notes") or "").rstrip()
                stamp = now_iso()
                addition = f"[Revisión cliente {client_name} {stamp}] {message}"
                t["notes"] = (existing_notes + "\n\n" + addition).strip() if existing_notes else addition
                editor_name = t.get("assignee", "")
                task_title = t.get("title") or t.get("client") or ""
                break
        kv_set(f"tenant:{tenant_id}:tasks", tasks)

        # 4) Mail al admin (Rafa) si tiene la pref activada
        tenant_doc = kv_get(f"tenant:{tenant_id}") or {}
        try:
            if tenant_doc.get("notify_on_revision") and tenant_doc.get("admin_email"):
                brand = tenant_doc.get("brand_name") or "Crudo"
                subject = f"📝 Revisión pedida por {client_name}"
                text = (f"{client_name} pidió un cambio sobre: {task_title}\n\n"
                        f"Mensaje:\n{message}\n\n"
                        f"Editor asignado: {editor_name or '(sin asignar)'}\n\n"
                        f"La tarea se marcó como urgente automáticamente.\n\n— {brand}")
                html = (f"<html><body style='font-family:-apple-system,Segoe UI,sans-serif;"
                        f"max-width:600px;color:#222;line-height:1.6;'>"
                        f"<h2 style='color:#f87171;'>📝 Revisión pedida por {client_name}</h2>"
                        f"<p><strong>Sobre:</strong> {task_title}</p>"
                        f"<div style='background:#fef3c7;border-left:3px solid #f59e0b;padding:12px 16px;border-radius:6px;margin:14px 0;color:#000;'>"
                        f"<strong>Mensaje del cliente:</strong><br>{message}</div>"
                        f"<p><strong>Editor asignado:</strong> {editor_name or '(sin asignar)'}</p>"
                        f"<p style='color:#666;font-size:13px;'>La tarea se marcó como 🚨 urgente automáticamente.</p>"
                        f"<hr><p style='color:#888;font-size:12px;'>— {brand}</p>"
                        f"</body></html>")
                notify_via_tenant(tenant_id, tenant_doc["admin_email"], subject, text, html)
        except Exception:
            pass

        # 5) Mail al editor asignado avisando del cambio
        try:
            editors = kv_get(f"tenant:{tenant_id}:editors") or []
            editor_obj = next((e for e in editors if e.get("name") == editor_name), None)
            if editor_obj and editor_obj.get("email"):
                brand = tenant_doc.get("brand_name") or "Crudo"
                subject = f"🚨 Revisión del cliente — {client_name}"
                text = (f"Hola {editor_name},\n\n{client_name} pidió un cambio sobre: {task_title}\n\n"
                        f"Mensaje del cliente:\n{message}\n\n"
                        f"La tarea está marcada como urgente.\n— {brand}")
                html = (f"<html><body style='font-family:-apple-system,Segoe UI,sans-serif;"
                        f"max-width:600px;color:#222;line-height:1.6;'>"
                        f"<h2 style='color:#f87171;'>🚨 Revisión del cliente</h2>"
                        f"<p>Hola <strong>{editor_name}</strong>, {client_name} pidió un cambio sobre:</p>"
                        f"<p style='font-size:16px;'><strong>{task_title}</strong></p>"
                        f"<div style='background:#fef3c7;border-left:3px solid #f59e0b;padding:12px 16px;border-radius:6px;margin:14px 0;color:#000;'>"
                        f"<strong>Pedido del cliente:</strong><br>{message}</div>"
                        f"<hr><p style='color:#888;font-size:12px;'>— {brand}</p>"
                        f"</body></html>")
                notify_via_tenant(tenant_id, editor_obj["email"], subject, text, html)
        except Exception:
            pass

        return json_response(self, {"ok": True})
