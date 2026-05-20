"""
GET  /api/weekly_digest?tenant=<id>&t=<token>           → preview JSON del digest
POST /api/weekly_digest?tenant=<id>&t=<token>  body: {send: true}
  → genera el digest Y manda mail al admin del tenant

Calcula sobre tasks + timeline + clients + editors:
  - tasks_completed_this_week (count + lista)
  - tasks_pending (count)
  - tasks_urgent (count)
  - top_editor_by_deliveries (name, count) — en la última semana
  - new_clients_this_week
  - comparison vs last week (% change en completions)
  - saturated_editors (pending_count > avg * 1.5)
  - oldest_pending_task (días)
"""

import datetime
import hashlib
import hmac
import os
from collections import Counter, defaultdict
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, json_response, read_json_body, notify_via_tenant


SECRET = os.environ.get("DASHBOARD_SECRET", "CHANGE_ME")

ACCENT = "#ff6b35"
BG = "#0f0f0f"
CARD_BG = "#1a1a1a"
TEXT = "#f5f5f5"
MUTED = "#999999"
BORDER = "#2a2a2a"


def verify_token(tenant_id, token):
    expected = hmac.new(SECRET.encode(), tenant_id.encode(), hashlib.sha256).hexdigest()[:24]
    return hmac.compare_digest(expected, token or "")


def _parse_iso(s):
    """Parsea un ISO string en datetime UTC naive. Devuelve None si no se puede."""
    if not s or not isinstance(s, str):
        return None
    try:
        s2 = s.rstrip("Z")
        # Soporta con o sin microseconds
        if "." in s2:
            return datetime.datetime.strptime(s2, "%Y-%m-%dT%H:%M:%S.%f")
        return datetime.datetime.strptime(s2, "%Y-%m-%dT%H:%M:%S")
    except Exception:
        try:
            return datetime.datetime.fromisoformat(s.replace("Z", ""))
        except Exception:
            return None


def compute_digest(tenant_id: str) -> dict:
    """Crunchea la data del tenant y devuelve un dict con todas las métricas."""
    tasks = kv_get(f"tenant:{tenant_id}:tasks") or []
    editors = kv_get(f"tenant:{tenant_id}:editors") or []
    clients = kv_get(f"tenant:{tenant_id}:clients") or []
    timeline = kv_get(f"tenant:{tenant_id}:timeline") or []

    now = datetime.datetime.utcnow()
    week_ago = now - datetime.timedelta(days=7)
    two_weeks_ago = now - datetime.timedelta(days=14)

    # ── Tasks ──────────────────────────────────────────────────────────────
    completed_this_week = []
    completed_last_week = []
    pending_tasks = []
    urgent_tasks = []

    for t in tasks:
        status = t.get("status", "pending")
        if status == "done":
            done_at = _parse_iso(t.get("completed_at"))
            if done_at:
                if done_at >= week_ago:
                    completed_this_week.append(t)
                elif done_at >= two_weeks_ago:
                    completed_last_week.append(t)
        else:
            pending_tasks.append(t)
            if t.get("urgent"):
                urgent_tasks.append(t)

    # ── Top editor (por entregas última semana) ───────────────────────────
    editor_deliveries = Counter()
    for t in completed_this_week:
        a = (t.get("assignee") or "").strip()
        if a:
            editor_deliveries[a] += 1
    top_editor = None
    if editor_deliveries:
        name, count = editor_deliveries.most_common(1)[0]
        top_editor = {"name": name, "count": count}

    # ── New clients (created_at en la última semana) ──────────────────────
    new_clients = []
    for c in clients:
        created = _parse_iso(c.get("created_at"))
        if created and created >= week_ago:
            new_clients.append(c)

    # ── Comparison vs last week ───────────────────────────────────────────
    n_this = len(completed_this_week)
    n_last = len(completed_last_week)
    if n_last == 0:
        pct_change = None if n_this == 0 else 100.0  # no había base
    else:
        pct_change = round(((n_this - n_last) / n_last) * 100.0, 1)

    # ── Saturated editors (pending_count > avg * 1.5) ─────────────────────
    pending_by_editor = defaultdict(int)
    for t in pending_tasks:
        a = (t.get("assignee") or "").strip()
        if a:
            pending_by_editor[a] += int(t.get("pending_count") or 1)
    saturated_editors = []
    if pending_by_editor:
        avg = sum(pending_by_editor.values()) / max(1, len(pending_by_editor))
        threshold = avg * 1.5
        for name, cnt in pending_by_editor.items():
            if cnt > threshold and cnt > 0:
                saturated_editors.append({"name": name, "pending_count": cnt, "avg": round(avg, 1)})
        saturated_editors.sort(key=lambda x: x["pending_count"], reverse=True)

    # ── Oldest pending task ───────────────────────────────────────────────
    oldest = None
    oldest_days = 0
    for t in pending_tasks:
        created = _parse_iso(t.get("created_at"))
        if not created:
            continue
        age = (now - created).days
        if age > oldest_days:
            oldest_days = age
            oldest = t

    oldest_pending = None
    if oldest:
        oldest_pending = {
            "id": oldest.get("id"),
            "client": oldest.get("client"),
            "title": oldest.get("title"),
            "assignee": oldest.get("assignee"),
            "days_old": oldest_days,
        }

    return {
        "tenant_id": tenant_id,
        "generated_at": now.isoformat() + "Z",
        "period": {
            "from": week_ago.isoformat() + "Z",
            "to": now.isoformat() + "Z",
        },
        "tasks_completed_this_week": {
            "count": len(completed_this_week),
            "list": [
                {
                    "id": t.get("id"),
                    "client": t.get("client"),
                    "title": t.get("title"),
                    "assignee": t.get("assignee"),
                    "completed_at": t.get("completed_at"),
                }
                for t in completed_this_week
            ],
        },
        "tasks_pending": len(pending_tasks),
        "tasks_urgent": len(urgent_tasks),
        "top_editor_by_deliveries": top_editor,
        "new_clients_this_week": [
            {"id": c.get("id"), "name": c.get("name"), "email": c.get("email")}
            for c in new_clients
        ],
        "comparison_vs_last_week": {
            "this_week": n_this,
            "last_week": n_last,
            "pct_change": pct_change,
        },
        "saturated_editors": saturated_editors,
        "oldest_pending_task": oldest_pending,
    }


# ─── Render ─────────────────────────────────────────────────────────────────

def _fmt_pct(p):
    if p is None:
        return "—"
    sign = "+" if p >= 0 else ""
    return f"{sign}{p}%"


def render_text(brand: str, d: dict) -> str:
    lines = []
    lines.append(f"DIGEST SEMANAL — {brand}")
    lines.append(f"Generado: {d.get('generated_at', '')}")
    lines.append("")
    lines.append(f"Tareas completadas esta semana: {d['tasks_completed_this_week']['count']}")
    lines.append(f"Tareas pendientes: {d['tasks_pending']}")
    lines.append(f"Tareas urgentes: {d['tasks_urgent']}")
    lines.append("")
    te = d.get("top_editor_by_deliveries")
    if te:
        lines.append(f"Top editor: {te['name']} ({te['count']} entregas)")
    else:
        lines.append("Top editor: —")
    cmp_ = d["comparison_vs_last_week"]
    lines.append(f"Vs semana pasada: {cmp_['this_week']} vs {cmp_['last_week']} ({_fmt_pct(cmp_['pct_change'])})")
    lines.append("")
    nc = d["new_clients_this_week"]
    lines.append(f"Clientes nuevos esta semana: {len(nc)}")
    for c in nc:
        lines.append(f"  - {c.get('name', '?')} ({c.get('email', '')})")
    lines.append("")
    sat = d["saturated_editors"]
    if sat:
        lines.append("Editores saturados:")
        for s in sat:
            lines.append(f"  - {s['name']}: {s['pending_count']} pendientes (avg {s['avg']})")
    else:
        lines.append("Editores saturados: ninguno")
    lines.append("")
    op = d.get("oldest_pending_task")
    if op:
        lines.append(f"Tarea pendiente más vieja: {op['title']} ({op['client']}) — {op['days_old']} días — asignada a {op.get('assignee') or 'nadie'}")
    lines.append("")
    completed = d["tasks_completed_this_week"]["list"]
    if completed:
        lines.append("DETALLE DE COMPLETADAS:")
        for t in completed:
            lines.append(f"  - [{t.get('client', '?')}] {t.get('title', '?')} — {t.get('assignee') or 'sin asignar'}")
    return "\n".join(lines)


def render_html(brand: str, d: dict) -> str:
    cmp_ = d["comparison_vs_last_week"]
    te = d.get("top_editor_by_deliveries")
    sat = d["saturated_editors"]
    op = d.get("oldest_pending_task")
    nc = d["new_clients_this_week"]
    completed = d["tasks_completed_this_week"]["list"]

    pct = cmp_["pct_change"]
    pct_color = MUTED
    if pct is not None:
        pct_color = "#4ade80" if pct >= 0 else "#f87171"

    def card(title, body):
        return f"""
        <div style="background:{CARD_BG};border:1px solid {BORDER};border-radius:12px;padding:20px;margin-bottom:16px;">
          <div style="color:{MUTED};font-size:11px;text-transform:uppercase;letter-spacing:1px;margin-bottom:8px;">{title}</div>
          {body}
        </div>
        """

    def stat_row(items):
        cells = "".join(
            f'<td style="padding:0 12px 0 0;vertical-align:top;">'
            f'<div style="color:{MUTED};font-size:11px;text-transform:uppercase;letter-spacing:1px;">{label}</div>'
            f'<div style="color:{TEXT};font-size:28px;font-weight:700;margin-top:4px;">{value}</div>'
            f'</td>'
            for label, value in items
        )
        return f'<table style="width:100%;border-collapse:collapse;"><tr>{cells}</tr></table>'

    stats_html = stat_row([
        ("Completadas", d["tasks_completed_this_week"]["count"]),
        ("Pendientes", d["tasks_pending"]),
        ("Urgentes", f'<span style="color:{ACCENT};">{d["tasks_urgent"]}</span>'),
    ])

    cmp_html = (
        f'<div style="color:{TEXT};font-size:16px;">'
        f'<strong>{cmp_["this_week"]}</strong> esta semana vs '
        f'<strong>{cmp_["last_week"]}</strong> la anterior '
        f'<span style="color:{pct_color};font-weight:700;margin-left:8px;">{_fmt_pct(pct)}</span>'
        f'</div>'
    )

    top_html = (
        f'<div style="color:{TEXT};font-size:18px;font-weight:700;">{te["name"]}</div>'
        f'<div style="color:{MUTED};font-size:13px;margin-top:4px;">{te["count"]} entregas esta semana</div>'
        if te else
        f'<div style="color:{MUTED};">Sin entregas esta semana.</div>'
    )

    nc_html = (
        "".join(
            f'<li style="color:{TEXT};margin-bottom:4px;">{c.get("name", "?")} '
            f'<span style="color:{MUTED};font-size:12px;">{c.get("email", "")}</span></li>'
            for c in nc
        ) or f'<li style="color:{MUTED};list-style:none;">Ninguno.</li>'
    )

    sat_html = (
        "".join(
            f'<li style="color:{TEXT};margin-bottom:4px;">{s["name"]} — '
            f'<strong style="color:{ACCENT};">{s["pending_count"]}</strong> pendientes '
            f'<span style="color:{MUTED};font-size:12px;">(avg {s["avg"]})</span></li>'
            for s in sat
        ) or f'<li style="color:{MUTED};list-style:none;">Ninguno, todo balanceado.</li>'
    )

    op_html = (
        f'<div style="color:{TEXT};font-size:14px;">'
        f'<strong>{op["title"]}</strong> — {op.get("client", "?")}<br>'
        f'<span style="color:{ACCENT};font-weight:700;">{op["days_old"]} días</span> '
        f'<span style="color:{MUTED};">asignada a {op.get("assignee") or "nadie"}</span>'
        f'</div>'
        if op else
        f'<div style="color:{MUTED};">Sin pendientes viejas.</div>'
    )

    completed_html = ""
    if completed:
        rows = "".join(
            f'<tr>'
            f'<td style="padding:8px 12px;border-bottom:1px solid {BORDER};color:{TEXT};font-size:13px;">{t.get("client", "?")}</td>'
            f'<td style="padding:8px 12px;border-bottom:1px solid {BORDER};color:{TEXT};font-size:13px;">{t.get("title", "?")}</td>'
            f'<td style="padding:8px 12px;border-bottom:1px solid {BORDER};color:{MUTED};font-size:13px;">{t.get("assignee") or "—"}</td>'
            f'</tr>'
            for t in completed
        )
        completed_html = f"""
        <table style="width:100%;border-collapse:collapse;margin-top:8px;">
          <thead>
            <tr>
              <th style="text-align:left;padding:8px 12px;color:{MUTED};font-size:11px;text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid {BORDER};">Cliente</th>
              <th style="text-align:left;padding:8px 12px;color:{MUTED};font-size:11px;text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid {BORDER};">Tarea</th>
              <th style="text-align:left;padding:8px 12px;color:{MUTED};font-size:11px;text-transform:uppercase;letter-spacing:1px;border-bottom:1px solid {BORDER};">Editor</th>
            </tr>
          </thead>
          <tbody>{rows}</tbody>
        </table>
        """
    else:
        completed_html = f'<div style="color:{MUTED};">Ninguna tarea completada esta semana.</div>'

    html = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:{BG};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <div style="max-width:640px;margin:0 auto;padding:32px 20px;background:{BG};">
    <div style="margin-bottom:24px;">
      <div style="color:{ACCENT};font-size:11px;text-transform:uppercase;letter-spacing:2px;font-weight:700;">Digest Semanal</div>
      <h1 style="color:{TEXT};font-size:28px;margin:6px 0 0 0;font-weight:700;">{brand}</h1>
      <div style="color:{MUTED};font-size:13px;margin-top:4px;">Resumen de los últimos 7 días</div>
    </div>

    {card("Resumen", stats_html)}
    {card("Comparativa semanal", cmp_html)}
    {card("Top editor", top_html)}
    {card("Clientes nuevos", f'<ul style="margin:0;padding-left:18px;">{nc_html}</ul>')}
    {card("Editores saturados", f'<ul style="margin:0;padding-left:18px;">{sat_html}</ul>')}
    {card("Tarea pendiente más vieja", op_html)}
    {card("Tareas completadas", completed_html)}

    <div style="text-align:center;color:{MUTED};font-size:11px;margin-top:24px;">
      Generado automáticamente por tu asistente
    </div>
  </div>
</body></html>"""
    return html


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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        try:
            digest = compute_digest(tenant_id)
        except Exception as e:
            return json_response(self, {"error": f"No pude computar el digest: {e}"}, 500)
        return json_response(self, {"digest": digest})

    def do_POST(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        try:
            data = read_json_body(self)
        except Exception:
            data = {}

        try:
            digest = compute_digest(tenant_id)
        except Exception as e:
            return json_response(self, {"error": f"No pude computar el digest: {e}"}, 500)

        send = bool(data.get("send"))
        sent = False
        send_error = None

        if send:
            tenant = kv_get(f"tenant:{tenant_id}") or {}
            to_email = (
                tenant.get("admin_email")
                or tenant.get("email")
                or tenant.get("owner_email")
                or ""
            ).strip()
            brand = tenant.get("brand_name") or tenant.get("business_name") or "Tu agencia"
            if not to_email:
                send_error = "El tenant no tiene email de admin configurado"
            else:
                subject = f"Digest semanal — {brand}"
                body_text = render_text(brand, digest)
                body_html = render_html(brand, digest)
                try:
                    sent = notify_via_tenant(tenant_id, to_email, subject, body_text, body_html)
                    if not sent:
                        send_error = "No pude mandar el mail (revisar OAuth refresh_token del tenant)"
                except Exception as e:
                    send_error = f"Error mandando mail: {e}"

        resp = {"digest": digest, "sent": sent}
        if send_error:
            resp["send_error"] = send_error
        return json_response(self, resp)
