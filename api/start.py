"""
POST /api/start
Body: { brand_name, admin_email, preset, [custom_*] }

Crea un pedido nuevo en KV con status="pending_oauth" y devuelve la URL
para hacer click en "Conectar Google". La carpeta de Drive se vincula
después en el dashboard, no acá.
"""

import re
from http.server import BaseHTTPRequestHandler

from _shared import (
    kv_set, new_pedido_id, base_url, google_oauth_url,
    json_response, read_json_body,
)


VALID_PRESETS = {
    "video_edit", "photo_studio", "design_agency", "ugc",
    "music_production", "events", "coaching",
    "custom",
}


def _extract_folder_id(url: str) -> str:
    """Saca el folder_id de una URL de Drive."""
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", url)
    return m.group(1) if m else ""


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        try:
            data = read_json_body(self)
        except Exception:
            return json_response(self, {"error": "JSON inválido"}, 400)

        brand_name = (data.get("brand_name") or "").strip()
        admin_email = (data.get("admin_email") or "").strip().lower()
        preset = (data.get("preset") or "").strip()

        # Validaciones
        if not brand_name or not admin_email or not preset:
            return json_response(self, {"error": "Faltan campos"}, 400)
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", admin_email):
            return json_response(self, {"error": "Email inválido"}, 400)
        if preset not in VALID_PRESETS:
            return json_response(self, {"error": "Tipo de negocio inválido"}, 400)

        # Si es custom, validar y armar los campos extras
        custom_fields = {}
        if preset == "custom":
            for fld in ("custom_description", "custom_input", "custom_output", "custom_assignee"):
                val = (data.get(fld) or "").strip()
                if not val:
                    return json_response(self, {"error": f"Falta {fld}"}, 400)
                custom_fields[fld] = val

        # Crear pedido en KV (sin drive folder — el cliente la va a vincular después en el dashboard)
        pedido_id = new_pedido_id()
        kv_set(f"pedido:{pedido_id}", {
            "id": pedido_id,
            "status": "pending_oauth",
            "brand_name": brand_name,
            "admin_email": admin_email,
            "preset": preset,
            "created_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            **custom_fields,
        })

        # URL para iniciar OAuth
        oauth_url = f"{base_url(self)}/api/oauth_init?id={pedido_id}"

        return json_response(self, {
            "pedido_id": pedido_id,
            "oauth_url": oauth_url,
        })
