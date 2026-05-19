"""
GET /api/status?id=<pedido_id>

Endpoint público para que el cliente vea el progreso de su pedido.
Devuelve SOLO datos públicos (sin tokens OAuth ni secrets).
"""

from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, json_response


# Estados que el cliente puede ver
STATUS_LABELS = {
    "pending_oauth":  {"label": "Esperando que conectes Google", "progress": 25},
    "oauth_done":     {"label": "Tu pedido está en cola — armando tu sistema", "progress": 50},
    "provisioning":   {"label": "Configurando tu instancia personal", "progress": 75},
    "deployed":       {"label": "¡Listo! Te llegó el link por mail", "progress": 100},
    "failed":         {"label": "Hubo un problema. Te contactamos por mail.", "progress": 0},
}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        pedido_id = (qs.get("id", [""])[0]).strip()

        if not pedido_id:
            return json_response(self, {"error": "Falta id"}, 400)

        pedido = kv_get(f"pedido:{pedido_id}")
        if not pedido:
            return json_response(self, {"error": "Pedido no encontrado o expirado"}, 404)

        status = pedido.get("status", "unknown")
        meta = STATUS_LABELS.get(status, {"label": status, "progress": 0})

        # Sólo devolver datos públicos
        return json_response(self, {
            "id": pedido_id,
            "brand_name": pedido.get("brand_name", ""),
            "status": status,
            "status_label": meta["label"],
            "progress": meta["progress"],
            "dashboard_url": pedido.get("dashboard_url", ""),
            "created_at": pedido.get("created_at", ""),
            "deployed_at": pedido.get("deployed_at", ""),
        })
