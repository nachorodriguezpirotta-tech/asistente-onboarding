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
    "oauth_done":     {"label": "Recibimos tu pedido — Nacho lo va a configurar", "progress": 50},
    "provisioning":   {"label": "Nacho está configurando tu sistema ahora", "progress": 75},
    "deployed":       {"label": "¡Listo! Revisá tu mail con el link", "progress": 100},
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
