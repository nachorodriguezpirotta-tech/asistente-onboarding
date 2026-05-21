"""
GET    /api/revisions?tenant=<id>&t=<token>&status=<open|resolved|all>  → lista revisiones
POST   /api/revisions?tenant=<id>&t=<token>  body: {task_id, message, client_name?}  → crear revisión MANUAL desde dashboard
PATCH  /api/revisions?tenant=<id>&t=<token>  body: {id, status?, resolution_note?}
DELETE /api/revisions?tenant=<id>&t=<token>&id=<rev_id>

Revisiones = pedidos de cambio. Vienen de 2 lados:
  1) Cliente desde el portal (client_portal.py)
  2) Manual desde el dashboard (POST acá)

Storage: KV key `tenant:{tenant_id}:revisions` es una lista de:
  {id, task_id, client_name, message, status, created_at, resolved_at, resolution_note, source}
"""

import datetime
import hashlib
import hmac
import os
import secrets as _secrets
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, kv_set, json_response, read_json_body, timeline_add, notify_via_tenant


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

    def do_POST(self):
        """Crear revisión MANUAL desde dashboard (no desde portal cliente)."""
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        try:
            data = read_json_body(self)
        except Exception:
            return json_response(self, {"error": "JSON inválido"}, 400)

        task_id = (data.get("task_id") or "").strip()
        message = (data.get("message") or "").strip()
        if not task_id or not message:
            return json_response(self, {"error": "Falta task_id o message"}, 400)

        # Buscar la task para sacar el cliente
        tasks = kv_get(f"tenant:{tenant_id}:tasks") or []
        task = next((t for t in tasks if t.get("id") == task_id), None)
        if not task:
            return json_response(self, {"error": "Tarea no encontrada"}, 404)

        client_name = (data.get("client_name") or task.get("client") or task.get("title") or "").strip() or "—"

        # Crear revisión
        rev = {
            "id": _secrets.token_urlsafe(6),
            "task_id": task_id,
            "client_name": client_name,
            "message": message,
            "status": "open",
            "created_at": now_iso(),
            "source": "manual",
        }
        revisions = kv_get(f"tenant:{tenant_id}:revisions") or []
        revisions.append(rev)
        kv_set(f"tenant:{tenant_id}:revisions", revisions)

        # Reabrir + urgent + append a notes (mismo flow que client_portal)
        editor_name = ""
        task_title = ""
        for t in tasks:
            if t.get("id") == task_id:
                t["urgent"] = True
                if t.get("status") == "done":
                    t["status"] = "pending"
                    t["pending_count"] = max(1, int(t.get("pending_count", 0)))
                existing_notes = (t.get("notes") or "").rstrip()
                stamp = now_iso()
                addition = f"[Revisión manual {stamp}] {message}"
                t["notes"] = (existing_notes + "\n\n" + addition).strip() if existing_notes else addition
                editor_name = t.get("assignee", "")
                task_title = t.get("title") or t.get("client") or ""
                break
        kv_set(f"tenant:{tenant_id}:tasks", tasks)

        # Timeline
        try:
            timeline_add(tenant_id, "revision_requested", client=client_name,
                         actor="admin (manual)",
                         payload={"task_id": task_id, "message_preview": message[:80], "source": "manual"})
        except Exception:
            pass

        # Mail al editor asignado avisando
        try:
            editors = kv_get(f"tenant:{tenant_id}:editors") or []
            editor_obj = next((e for e in editors if e.get("name") == editor_name), None)
            tenant_doc = kv_get(f"tenant:{tenant_id}") or {}
            if editor_obj and editor_obj.get("email"):
                brand = tenant_doc.get("brand_name") or "Asistente"
                subject = f"🚨 Revisión — {client_name}"
                text = (f"Hola {editor_name},\n\nHay una revisión cargada sobre: {task_title}\n\n"
                        f"Pedido:\n{message}\n\nLa tarea está marcada como urgente.\n— {brand}")
                html = (f"<html><body style='font-family:-apple-system,Segoe UI,sans-serif;"
                        f"max-width:600px;color:#222;line-height:1.6;'>"
                        f"<h2 style='color:#f87171;'>🚨 Revisión</h2>"
                        f"<p>Hola <strong>{editor_name}</strong>, hay una revisión cargada sobre:</p>"
                        f"<p style='font-size:16px;'><strong>{task_title}</strong></p>"
                        f"<div style='background:#fef3c7;border-left:3px solid #f59e0b;padding:12px 16px;"
                        f"border-radius:6px;margin:14px 0;color:#000;'>"
                        f"<strong>Pedido:</strong><br>{message}</div>"
                        f"<hr><p style='color:#888;font-size:12px;'>— {brand}</p>"
                        f"</body></html>")
                notify_via_tenant(tenant_id, editor_obj["email"], subject, text, html)
        except Exception:
            pass

        return json_response(self, {"ok": True, "revision": rev})

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
        prev_status = None
        for r in revisions:
            if r.get("id") == rid:
                prev_status = r.get("status")
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

        # Mail al admin cuando la revisión pasa de open → resolved
        if prev_status != "resolved" and updated.get("status") == "resolved":
            try:
                tenant = kv_get(f"tenant:{tenant_id}") or {}
                if tenant.get("notify_on_revision") and tenant.get("admin_email"):
                    brand = tenant.get("brand_name") or "Asistente"
                    cli = updated.get("client_name", "—")
                    msg = updated.get("message", "")
                    resnote = updated.get("resolution_note", "")
                    # Buscar editor de la task
                    tasks = kv_get(f"tenant:{tenant_id}:tasks") or []
                    task = next((t for t in tasks if t.get("id") == updated.get("task_id")), None)
                    editor = (task or {}).get("assignee", "—")
                    task_title = (task or {}).get("title") or (task or {}).get("client") or "—"

                    subject = f"✅ Revisión resuelta — {cli}"
                    resnote_text = ("Nota de resolución: " + resnote) if resnote else ""
                    text = (f"La revisión de {cli} sobre '{task_title}' quedó resuelta.\n\n"
                            f"Pedido original: {msg}\n"
                            f"{resnote_text}\n\n"
                            f"Editor: {editor}\n\n— {brand}")
                    resnote_html = ""
                    if resnote:
                        resnote_html = ("<div style='background:rgba(74,222,128,0.1);border-left:3px solid #4ade80;"
                                        "padding:10px 14px;border-radius:6px;margin:10px 0;color:#000;font-size:13px;'>"
                                        f"<strong>Resolución:</strong><br>{resnote}</div>")
                    html = (f"<html><body style='font-family:-apple-system,Segoe UI,sans-serif;"
                            f"max-width:600px;color:#222;line-height:1.6;'>"
                            f"<h2 style='color:#4ade80;'>✅ Revisión resuelta</h2>"
                            f"<p><strong>{cli}</strong> — <em>{task_title}</em></p>"
                            f"<div style='background:#f3f4f6;border-left:3px solid #888;padding:10px 14px;"
                            f"border-radius:6px;margin:10px 0;color:#000;font-size:13px;'>"
                            f"<strong>Pedido original:</strong><br>{msg}</div>"
                            f"{resnote_html}"
                            f"<p>Editor: <strong>{editor}</strong></p>"
                            f"<hr><p style='color:#888;font-size:12px;'>— {brand}</p>"
                            f"</body></html>")
                    notify_via_tenant(tenant_id, tenant["admin_email"], subject, text, html)
            except Exception:
                pass

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
