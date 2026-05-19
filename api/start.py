"""
POST /api/start
Body: { brand_name, admin_email, preset, drive_url }

Crea un pedido nuevo en KV con status="pending_oauth" y devuelve la URL
para hacer click en "Conectar Google".
"""

import re
from http.server import BaseHTTPRequestHandler

from _shared import (
    kv_set, new_pedido_id, base_url, google_oauth_url,
    json_response, read_json_body,
)


VALID_PRESETS = {"video_edit", "photo_studio", "design_agency", "ugc", "accounting", "legal", "custom"}


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
        drive_mode = (data.get("drive_mode") or "folder").strip()  # 'folder' o 'my_drive'
        drive_url = (data.get("drive_url") or "").strip()

        # Validaciones
        if not brand_name or not admin_email or not preset:
            return json_response(self, {"error": "Faltan campos"}, 400)
        if drive_mode not in ("folder", "my_drive"):
            return json_response(self, {"error": "drive_mode inválido"}, 400)
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", admin_email):
            return json_response(self, {"error": "Email inválido"}, 400)
        if preset not in VALID_PRESETS:
            return json_response(self, {"error": "Tipo de negocio inválido"}, 400)

        # Folder ID: solo si drive_mode='folder'. Si es my_drive, dejamos vacío
        # y el sistema watchea raíz de la unidad.
        folder_id = ""
        if drive_mode == "folder":
            folder_id = _extract_folder_id(drive_url)
            if not folder_id:
                return json_response(self, {"error": "Link de Drive inválido. Debe contener /folders/<id>."}, 400)

        # Si es custom, validar y armar los campos extras
        custom_fields = {}
        if preset == "custom":
            for fld in ("custom_description", "custom_input", "custom_output", "custom_assignee"):
                val = (data.get(fld) or "").strip()
                if not val:
                    return json_response(self, {"error": f"Falta {fld}"}, 400)
                custom_fields[fld] = val

        # Crear pedido en KV
        pedido_id = new_pedido_id()
        kv_set(f"pedido:{pedido_id}", {
            "id": pedido_id,
            "status": "pending_oauth",
            "brand_name": brand_name,
            "admin_email": admin_email,
            "preset": preset,
            "drive_mode": drive_mode,
            "drive_url": drive_url,
            "drive_folder_id": folder_id,
            "created_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            **custom_fields,
        })

        # URL para iniciar OAuth
        oauth_url = f"{base_url(self)}/api/oauth_init?id={pedido_id}"

        return json_response(self, {
            "pedido_id": pedido_id,
            "oauth_url": oauth_url,
        })
