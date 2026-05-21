"""
POST /api/remind_editor?tenant=<id>&t=<token>
  body: {editor_id} | {editor_name}

Manda mail a UN editor con su lista de pendientes (incluye días que llevan).
Llamado manualmente desde el dashboard.

POST /api/remind_editor?tenant=<id>&t=<token>&all_daily=1
  Sin body. Manda mail SOLO a los editores que tienen daily_reminder=true.
  Usado por el cron diario.
"""

import datetime
import hashlib
import hmac
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, json_response, read_json_body, notify_via_tenant


SECRET = os.environ.get("DASHBOARD_SECRET", "CHANGE_ME")


def verify_token(tenant_id, token):
    expected = hmac.new(SECRET.encode(), tenant_id.encode(), hashlib.sha256).hexdigest()[:24]
    return hmac.compare_digest(expected, token or "")


def _h(s):
    s = str(s or "")
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _days_old(created_at_iso: str) -> int:
    if not created_at_iso:
        return 0
    try:
        s = created_at_iso.replace("Z", "+00:00")
        if "." in s:
            base, _, rest = s.partition(".")
            if "+" in rest:
                tz = "+" + rest.split("+", 1)[1]
            elif "-" in rest:
                tz = "-" + rest.split("-", 1)[1]
            else:
                tz = "+00:00"
            s = base + tz
        dt = datetime.datetime.fromisoformat(s)
        now = datetime.datetime.now(datetime.timezone.utc)
        return (now - dt).days
    except Exception:
        return 0


def build_editor_reminder(tenant: dict, editor: dict, tasks: list, assignments: list, base_host: str = "") -> tuple:
    """Devuelve (subject, text, html) con el resumen de pendientes del editor."""
    brand = tenant.get("brand_name") or "Asistente"
    editor_name = editor.get("name", "Editor")
    editor_tasks = [t for t in tasks if t.get("assignee") == editor_name and t.get("status") != "done"]

    # Ordenar urgentes primero, luego más viejos primero
    editor_tasks.sort(key=lambda t: (not t.get("urgent"), t.get("created_at", "")))

    total = len(editor_tasks)
    urgent = sum(1 for t in editor_tasks if t.get("urgent"))

    # Asignaciones map (para link Drive)
    assignments_by_client = {}
    for a in assignments:
        fn = (a.get("folder_name") or "").lower()
        if fn:
            assignments_by_client[fn] = a.get("folder_id", "")

    if total == 0:
        subject = f"🎉 No tenés pendientes — {brand}"
        text = f"Hola {editor_name},\n\nNo tenés pendientes hoy. 🎉\n\n— {brand}"
        html = (f"<html><body style='font-family:-apple-system,Segoe UI,sans-serif;max-width:600px;color:#222;line-height:1.6;'>"
                f"<h2 style='color:#4ade80;'>🎉 Sin pendientes</h2>"
                f"<p>Hola <strong>{_h(editor_name)}</strong>, hoy estás al día. Buen trabajo.</p>"
                f"<hr><p style='color:#888;font-size:12px;'>— {_h(brand)}</p>"
                f"</body></html>")
        return subject, text, html

    subject = f"📋 Tus pendientes ({total}{' · ' + str(urgent) + ' urgentes' if urgent else ''}) — {brand}"

    # ── Texto plano ──
    text_lines = [f"Hola {editor_name},", "", f"Tenés {total} pendiente(s). Esto es lo que hay:", ""]
    for i, t in enumerate(editor_tasks, 1):
        cli = t.get("client") or t.get("title") or "—"
        count = int(t.get("pending_count") or 1)
        days = _days_old(t.get("created_at", ""))
        urg = "🚨 " if t.get("urgent") else ""
        days_label = f"hace {days} día{'s' if days != 1 else ''}" if days > 0 else "hoy"
        text_lines.append(f"{i}. {urg}{cli} — {count} pendiente{'s' if count != 1 else ''} · {days_label}")
        if t.get("notes"):
            text_lines.append(f"   📝 {t['notes']}")
    text_lines += ["", f"— {brand}"]
    text = "\n".join(text_lines)

    # ── HTML ──
    html_rows = []
    for t in editor_tasks:
        cli = t.get("client") or t.get("title") or "—"
        count = int(t.get("pending_count") or 1)
        days = _days_old(t.get("created_at", ""))
        urg = t.get("urgent")
        days_label = f"hace {days} día{'s' if days != 1 else ''}" if days > 0 else "hoy"
        days_color = "#f87171" if days >= 4 else ("#fbbf24" if days >= 2 else "#888")
        notes = t.get("notes", "")

        # link Drive si tenemos el folder
        folder_id = t.get("source_folder_id") or assignments_by_client.get(cli.lower(), "")
        folder_link = f"https://drive.google.com/drive/folders/{folder_id}" if folder_id else ""

        html_rows.append(f"""
        <tr><td style="padding:14px 0;border-bottom:1px solid #eee;">
          <div style="display:flex;align-items:flex-start;gap:10px;">
            <div style="flex:1;">
              <div style="font-weight:600;color:#222;font-size:15px;">
                {('🚨 ' if urg else '')}{_h(cli)}
                <span style="background:#fef3c7;color:#92400e;padding:2px 8px;border-radius:100px;font-size:11px;font-weight:700;margin-left:4px;">{count} pendiente{'s' if count != 1 else ''}</span>
              </div>
              <div style="color:{days_color};font-size:12px;margin-top:3px;">⏱ {_h(days_label)}</div>
              {f'<div style="color:#666;font-size:12px;margin-top:4px;">📝 {_h(notes)}</div>' if notes else ''}
              {f'<div style="margin-top:6px;"><a href="{_h(folder_link)}" style="color:#ff6b35;text-decoration:none;font-size:12px;font-weight:600;">📁 Abrir carpeta en Drive →</a></div>' if folder_link else ''}
            </div>
          </div>
        </td></tr>
        """)

    html = f"""<html><body style="font-family:-apple-system,Segoe UI,sans-serif;max-width:600px;margin:0 auto;color:#222;line-height:1.5;padding:20px;background:#fafafa;">
<div style="background:#fff;border-radius:12px;padding:24px;border:1px solid #eee;">
  <h2 style="color:#ff6b35;margin:0 0 4px;">📋 Tus pendientes</h2>
  <p style="color:#666;margin:0 0 20px;font-size:13px;">Hola <strong style="color:#222;">{_h(editor_name)}</strong>, esto es lo que tenés en cola{' · ' + str(urgent) + ' urgentes' if urgent else ''}.</p>
  <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
    {''.join(html_rows)}
  </table>
</div>
<p style="color:#888;font-size:11px;text-align:center;margin-top:16px;">— {_h(brand)}</p>
</body></html>"""

    return subject, text, html


def send_to_editor(tenant_id: str, tenant: dict, editor: dict, tasks: list, assignments: list, host: str = "") -> dict:
    email = (editor.get("email") or "").strip()
    if not email:
        return {"editor": editor.get("name"), "sent": False, "reason": "sin email"}
    if editor.get("on_vacation"):
        return {"editor": editor.get("name"), "sent": False, "reason": "en vacaciones"}
    subject, text, html = build_editor_reminder(tenant, editor, tasks, assignments, host)
    ok = notify_via_tenant(tenant_id, email, subject, text, html)
    return {"editor": editor.get("name"), "sent": ok}


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
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)

        qs = parse_qs(urlparse(self.path).query)
        all_daily = qs.get("all_daily", [""])[0] == "1"

        tenant = kv_get(f"tenant:{tenant_id}") or {}
        editors = kv_get(f"tenant:{tenant_id}:editors") or []
        tasks = kv_get(f"tenant:{tenant_id}:tasks") or []
        assignments = kv_get(f"tenant:{tenant_id}:folder_assignments") or []
        host = self.headers.get("Host", "")

        if all_daily:
            # Mandar a TODOS los editores con daily_reminder=true
            results = []
            for e in editors:
                if not e.get("daily_reminder"):
                    continue
                r = send_to_editor(tenant_id, tenant, e, tasks, assignments, host)
                results.append(r)
            return json_response(self, {"ok": True, "results": results, "total": len(results)})

        # Manual: mandar a un editor específico
        try:
            data = read_json_body(self)
        except Exception:
            data = {}
        target_id = (data.get("editor_id") or "").strip()
        target_name = (data.get("editor_name") or "").strip()
        if not target_id and not target_name:
            return json_response(self, {"error": "Falta editor_id o editor_name"}, 400)

        editor = None
        for e in editors:
            if (target_id and e.get("id") == target_id) or (target_name and e.get("name") == target_name):
                editor = e
                break
        if not editor:
            return json_response(self, {"error": "Editor no encontrado"}, 404)

        result = send_to_editor(tenant_id, tenant, editor, tasks, assignments, host)
        return json_response(self, {"ok": True, **result})
