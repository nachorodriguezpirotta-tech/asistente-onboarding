"""
GET /api/tenant_stats?tenant=<id>&t=<token>

Métricas del tenant:
  - Por editor: pending_count, clientes con pendientes, entregados semana/mes,
    turnaround promedio, días desde la pending más vieja, health (ok/warn/crit)
  - Globales: totales agregados
"""

import datetime
import hashlib
import hmac
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, json_response


SECRET = os.environ.get("DASHBOARD_SECRET", "CHANGE_ME")


def verify_token(tenant_id, token):
    expected = hmac.new(SECRET.encode(), tenant_id.encode(), hashlib.sha256).hexdigest()[:24]
    return hmac.compare_digest(expected, token or "")


def _parse(s):
    if not s:
        return None
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "").split(".")[0])
    except Exception:
        return None


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        tenant_id = (qs.get("tenant", [""])[0]).strip()
        token = (qs.get("t", [""])[0]).strip()
        if not verify_token(tenant_id, token):
            return json_response(self, {"error": "Token inválido"}, 403)

        editors = kv_get(f"tenant:{tenant_id}:editors") or []
        tasks = kv_get(f"tenant:{tenant_id}:tasks") or []

        now = datetime.datetime.utcnow()
        week_ago = now - datetime.timedelta(days=7)
        month_ago = now - datetime.timedelta(days=30)

        by_editor = {}
        for e in editors:
            name = e.get("name")
            by_editor[name] = {
                "name": name,
                "email": e.get("email", ""),
                "on_vacation": bool(e.get("on_vacation", False)),
                "pending_count": 0,           # suma de pending_count de cada task pending
                "pending_clients": 0,          # tasks pending
                "urgent_count": 0,
                "delivered_week": 0,
                "delivered_month": 0,
                "avg_turnaround_hours": 0,
                "turnaround_samples": [],
                "oldest_pending_days": 0,
            }

        oldest_per_editor = {}

        for t in tasks:
            ed = t.get("assignee") or ""
            if ed not in by_editor:
                continue  # tarea asignada a editor que ya no existe
            if t.get("status") == "pending":
                by_editor[ed]["pending_clients"] += 1
                by_editor[ed]["pending_count"] += int(t.get("pending_count", 1)) or 1
                if t.get("urgent"):
                    by_editor[ed]["urgent_count"] += 1
                created = _parse(t.get("created_at"))
                if created:
                    cur = oldest_per_editor.get(ed)
                    if cur is None or created < cur:
                        oldest_per_editor[ed] = created
            elif t.get("status") == "done":
                completed = _parse(t.get("completed_at"))
                if not completed:
                    continue
                if completed >= week_ago:
                    by_editor[ed]["delivered_week"] += 1
                if completed >= month_ago:
                    by_editor[ed]["delivered_month"] += 1
                # turnaround
                created = _parse(t.get("created_at"))
                if created and completed >= month_ago:
                    delta = (completed - created).total_seconds() / 3600
                    if delta >= 0:
                        by_editor[ed]["turnaround_samples"].append(delta)

        # Calcular averages y oldest
        for ed, data in by_editor.items():
            samples = data.pop("turnaround_samples", [])
            data["avg_turnaround_hours"] = round(sum(samples) / len(samples), 1) if samples else 0
            old = oldest_per_editor.get(ed)
            if old:
                data["oldest_pending_days"] = (now - old).days
            # Health
            if data["oldest_pending_days"] >= 10:
                data["health"] = "critical"
            elif data["oldest_pending_days"] >= 5 or data["urgent_count"] > 0:
                data["health"] = "warning"
            else:
                data["health"] = "ok"

        # Totales globales
        totals = {
            "total_pending": sum(d["pending_count"] for d in by_editor.values()),
            "total_pending_clients": sum(d["pending_clients"] for d in by_editor.values()),
            "total_urgent": sum(d["urgent_count"] for d in by_editor.values()),
            "delivered_week": sum(d["delivered_week"] for d in by_editor.values()),
            "delivered_month": sum(d["delivered_month"] for d in by_editor.values()),
            "active_editors": sum(1 for e in editors if not e.get("on_vacation")),
            "vacationing_editors": sum(1 for e in editors if e.get("on_vacation")),
        }

        # Ranking por entregados del mes
        top_editors = sorted(by_editor.values(), key=lambda x: -x["delivered_month"])[:10]

        return json_response(self, {
            "totals": totals,
            "by_editor": list(by_editor.values()),
            "top_editors": top_editors,
        })
