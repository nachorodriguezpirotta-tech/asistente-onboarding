"""
GET /api/drive_folder_info?tenant=<id>&t=<token>&folder_id=<id_o_url>

Devuelve la info de UNA carpeta de Drive (nombre, modified, etc).
Acepta tanto folder_id como una URL completa de Drive (la parsea).
"""

import hashlib
import hmac
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, json_response


SECRET = os.environ.get("DASHBOARD_SECRET", "CHANGE_ME")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")


def verify_token(tenant_id, token):
    expected = hmac.new(SECRET.encode(), tenant_id.encode(), hashlib.sha256).hexdigest()[:24]
    return hmac.compare_digest(expected, token or "")


def refresh_access_token(refresh_token: str) -> str:
    data = urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["access_token"]


def extract_folder_id(input_str: str) -> str:
    """Si es una URL de Drive, extrae el folder_id. Si es solo el id, lo devuelve."""
    s = input_str.strip()
    # URLs típicas:
    # https://drive.google.com/drive/folders/<ID>
    # https://drive.google.com/drive/folders/<ID>?usp=sharing
    # https://drive.google.com/drive/u/0/folders/<ID>
    m = re.search(r"/folders/([a-zA-Z0-9_-]+)", s)
    if m:
        return m.group(1)
    # Si no es URL, asumir que ya es el ID (sin slashes ni espacios)
    if re.match(r"^[a-zA-Z0-9_-]+$", s):
        return s
    return ""


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        tenant_id = (qs.get("tenant", [""])[0]).strip()
        token = (qs.get("t", [""])[0]).strip()
        folder_input = (qs.get("folder_id", [""])[0]).strip()

        if not verify_token(tenant_id, token):
            return json_response(self, {"error": "Token inválido"}, 403)

        folder_id = extract_folder_id(folder_input)
        if not folder_id:
            return json_response(self, {"error": "No pude extraer el folder ID. Asegurate que el link sea de una carpeta de Drive (debe contener /folders/...)."}, 400)

        tenant = kv_get(f"tenant:{tenant_id}")
        if not tenant:
            return json_response(self, {"error": "Tenant no encontrado"}, 404)

        refresh = tenant.get("oauth_refresh_token", "")
        if not refresh:
            return json_response(self, {"error": "Sin OAuth tokens"}, 400)

        try:
            access_token = refresh_access_token(refresh)
            url = f"https://www.googleapis.com/drive/v3/files/{folder_id}?fields=id,name,mimeType,parents,modifiedTime&supportsAllDrives=true"
            req = urllib.request.Request(url)
            req.add_header("Authorization", f"Bearer {access_token}")
            with urllib.request.urlopen(req, timeout=10) as r:
                data = json.loads(r.read())

            # Verificar que sea carpeta
            if data.get("mimeType") != "application/vnd.google-apps.folder":
                return json_response(self, {"error": "Ese link no es de una carpeta. Asegurate de copiar el link de una CARPETA, no un archivo."}, 400)

            return json_response(self, {
                "id": data["id"],
                "name": data["name"],
                "modified": data.get("modifiedTime", ""),
            })
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            if e.code == 404:
                return json_response(self, {"error": "No encontré esa carpeta. ¿La carpeta está compartida con la cuenta Google conectada?"}, 404)
            if e.code == 403:
                return json_response(self, {"error": "Sin permisos para ver esa carpeta. Verificá que esté compartida con tu cuenta Google."}, 403)
            return json_response(self, {"error": f"Drive API error {e.code}"}, 500)
        except Exception as e:
            return json_response(self, {"error": str(e)}, 500)
