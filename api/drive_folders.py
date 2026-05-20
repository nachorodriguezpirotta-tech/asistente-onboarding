"""
GET /api/drive_folders?tenant=<id>&t=<token>&parent=<folder_id>

Lista carpetas del Drive del tenant. Si parent es 'root' o vacío, lista las
carpetas de Mi Unidad. Si parent es un folder_id, lista las subcarpetas.

Usa el OAuth refresh_token guardado del tenant para autenticarse.
"""

import hashlib
import hmac
import json
import os
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
    """Convierte refresh_token → access_token (válido 1h)."""
    data = urllib.parse.urlencode({
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())["access_token"]


def list_drive_folders(access_token: str, parent: str = "root", limit: int = 100):
    """Lista carpetas inmediatas debajo de parent."""
    # Query: folders, no en papelera, padre = parent
    q = f"mimeType='application/vnd.google-apps.folder' and trashed=false and '{parent}' in parents"
    params = {
        "q": q,
        "fields": "files(id,name,parents,modifiedTime),nextPageToken",
        "pageSize": str(limit),
        "orderBy": "name",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    }
    url = "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {access_token}")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read()).get("files", [])


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
        parent = (qs.get("parent", ["root"])[0]).strip() or "root"

        if not verify_token(tenant_id, token):
            return json_response(self, {"error": "Token inválido"}, 403)

        tenant = kv_get(f"tenant:{tenant_id}")
        if not tenant:
            return json_response(self, {"error": "Tenant no encontrado"}, 404)

        refresh = tenant.get("oauth_refresh_token", "")
        if not refresh:
            return json_response(self, {"error": "Sin OAuth tokens"}, 400)

        try:
            access_token = refresh_access_token(refresh)
            folders = list_drive_folders(access_token, parent)
            return json_response(self, {
                "folders": [
                    {"id": f["id"], "name": f["name"], "modified": f.get("modifiedTime", "")}
                    for f in folders
                ],
                "parent": parent,
            })
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace")
            return json_response(self, {"error": f"Drive API {e.code}: {body[:200]}"}, 500)
        except Exception as e:
            return json_response(self, {"error": str(e)}, 500)
