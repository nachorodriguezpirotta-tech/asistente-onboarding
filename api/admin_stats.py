"""
GET /api/admin_stats?t=<admin_token>

Devuelve stats agregadas:
  - Visitas a landing por día (últimos 30)
  - Wizard starts
  - OAuth completados
  - Waitlist signups
  - Lista de waitlist
"""

import os
import datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, _kv_request, json_response


ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        token = (qs.get("t", [""])[0]).strip()

        if not ADMIN_TOKEN or token != ADMIN_TOKEN:
            return json_response(self, {"error": "Token inválido"}, 403)

        # Stats por día — últimos 30
        days = []
        today = datetime.datetime.utcnow().date()
        for i in range(30):
            d = today - datetime.timedelta(days=i)
            ds = d.strftime("%Y-%m-%d")
            row = {"date": ds, "pageview": 0, "waitlist_signup": 0,
                   "wizard_start": 0, "oauth_completed": 0}
            for event in ("pageview", "waitlist_signup", "wizard_start", "oauth_completed"):
                try:
                    r = _kv_request(["GET", f"track:{ds}:{event}"])
                    row[event] = int(r.get("result") or 0)
                except Exception:
                    pass
            days.append(row)

        # Waitlist — listar
        waitlist = []
        try:
            r = _kv_request(["KEYS", "waitlist:*"])
            keys = r.get("result", []) or []
            for k in keys:
                w = kv_get(k)
                if w:
                    waitlist.append(w)
            waitlist.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        except Exception:
            pass

        # Totales
        totals = {
            "total_pageviews": sum(d["pageview"] for d in days),
            "total_waitlist": len(waitlist),
            "total_wizard_starts": sum(d["wizard_start"] for d in days),
            "total_oauth_completed": sum(d["oauth_completed"] for d in days),
        }

        return json_response(self, {
            "totals": totals,
            "days": days,
            "waitlist": waitlist,
        })
