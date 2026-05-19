"""
GET /api/lookup?email=<email>

Endpoint público que devuelve los pedidos asociados a un email.
NO devuelve tokens — solo metadata pública.
"""

from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, _kv_request, json_response


# Mismas labels que /api/status
STATUS_LABELS = {
    "pending_oauth":  "Esperando que conectes Google",
    "oauth_done":     "Tu pedido está en cola — armando tu sistema",
    "provisioning":   "Configurando tu instancia personal",
    "deployed":       "¡Listo! Te llegó el link por mail",
    "failed":         "Hubo un problema. Te contactamos por mail.",
}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        email = (qs.get("email", [""])[0]).strip().lower()

        if not email or "@" not in email:
            return json_response(self, {"error": "Email inválido"}, 400)

        # Listar todas las keys de pedidos
        try:
            keys_r = _kv_request(["KEYS", "pedido:*"])
            keys = keys_r.get("result", []) or []
        except Exception as e:
            return json_response(self, {"error": f"KV: {e}"}, 500)

        # Filtrar por email
        out = []
        for k in keys:
            p = kv_get(k)
            if not p:
                continue
            if (p.get("admin_email") or "").lower() == email:
                status = p.get("status", "unknown")
                out.append({
                    "id": p.get("id"),
                    "brand_name": p.get("brand_name"),
                    "status": status,
                    "status_label": STATUS_LABELS.get(status, status),
                    "created_at": p.get("created_at"),
                    "deployed_at": p.get("deployed_at"),
                    "dashboard_url": p.get("dashboard_url", ""),
                })

        out.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        return json_response(self, {"pedidos": out, "total": len(out)})
