"""
GET    /api/folder_assignments?tenant=<id>&t=<token>           → lista
POST   /api/folder_assignments?tenant=<id>&t=<token>  body: {folder_id, folder_name, editor_id, editor_name}  → asignar (hace baseline)
DELETE /api/folder_assignments?tenant=<id>&t=<token>&folder_id=<id>  → desasignar

Al crear un assignment NUEVO, hace baseline scan de la carpeta:
marca todos los archivos actuales como "ya conocidos" (no genera tareas para ellos).
Solo los archivos que se suban DESPUÉS van a generar tareas.
"""

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, kv_set, json_response, read_json_body


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


def list_all_file_ids_in_folder(access_token: str, folder_id: str) -> list:
    """Lista TODOS los archivos (no carpetas) de la carpeta. Devuelve solo IDs."""
    q = f"'{folder_id}' in parents and mimeType != 'application/vnd.google-apps.folder' and trashed=false"
    params = {
        "q": q, "fields": "files(id)", "pageSize": "1000",
        "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
    }
    url = "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {access_token}")
    with urllib.request.urlopen(req, timeout=15) as r:
        return [f["id"] for f in json.loads(r.read()).get("files", [])]


def do_baseline(tenant_id: str, folder_id: str):
    """Marca todos los archivos actuales de la carpeta como conocidos."""
    tenant = kv_get(f"tenant:{tenant_id}")
    if not tenant:
        return 0
    refresh = tenant.get("oauth_refresh_token", "")
    if not refresh:
        return 0
    try:
        access_token = refresh_access_token(refresh)
        existing_ids = list_all_file_ids_in_folder(access_token, folder_id)
    except Exception as e:
        print(f"⚠️  Baseline falló para {folder_id}: {e}")
        return 0

    known = set(kv_get(f"tenant:{tenant_id}:known_files") or [])
    known.update(existing_ids)
    kv_set(f"tenant:{tenant_id}:known_files", list(known))
    return len(existing_ids)


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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        assignments = kv_get(f"tenant:{tenant_id}:folder_assignments") or []
        return json_response(self, {"assignments": assignments})

    def do_POST(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        try:
            data = read_json_body(self)
        except Exception:
            return json_response(self, {"error": "JSON inválido"}, 400)

        folder_id = (data.get("folder_id") or "").strip()
        folder_name = (data.get("folder_name") or "").strip()
        editor_id = (data.get("editor_id") or "").strip()
        editor_name = (data.get("editor_name") or "").strip()

        if not folder_id or not editor_id:
            return json_response(self, {"error": "Falta folder_id o editor_id"}, 400)

        # Carpeta de editados (output) opcional
        output_folder_id = (data.get("output_folder_id") or "").strip()
        output_folder_name = (data.get("output_folder_name") or "").strip()

        assignments = kv_get(f"tenant:{tenant_id}:folder_assignments") or []
        existing = next((a for a in assignments if a.get("folder_id") == folder_id), None)
        is_new = existing is None

        # Remove existing, append new
        assignments = [a for a in assignments if a.get("folder_id") != folder_id]
        new_assignment = {
            "folder_id": folder_id,
            "folder_name": folder_name,
            "editor_id": editor_id,
            "editor_name": editor_name,
        }
        if output_folder_id:
            new_assignment["output_folder_id"] = output_folder_id
            new_assignment["output_folder_name"] = output_folder_name
        assignments.append(new_assignment)
        kv_set(f"tenant:{tenant_id}:folder_assignments", assignments)

        # Baseline: marcar archivos actuales como conocidos
        # (input + output si existen)
        baseline_count = 0
        if is_new:
            baseline_count += do_baseline(tenant_id, folder_id)
            if output_folder_id:
                baseline_count += do_baseline(tenant_id, output_folder_id)

        msg = f"Carpeta vinculada. {baseline_count} archivos existentes marcados como baseline (no van a generar tareas). Solo los NUEVOS van a aparecer."
        if not is_new:
            msg = "Carpeta re-asignada."
            if output_folder_id and (not existing or not existing.get("output_folder_id")):
                msg += f" + agregada carpeta de editados."
                baseline_count = do_baseline(tenant_id, output_folder_id)

        return json_response(self, {
            "ok": True,
            "assignments": assignments,
            "baseline_files": baseline_count,
            "message": msg,
        })

    def do_DELETE(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)
        qs = parse_qs(urlparse(self.path).query)
        folder_id = (qs.get("folder_id", [""])[0]).strip()
        if not folder_id:
            return json_response(self, {"error": "Falta folder_id"}, 400)
        assignments = kv_get(f"tenant:{tenant_id}:folder_assignments") or []
        new = [a for a in assignments if a.get("folder_id") != folder_id]
        kv_set(f"tenant:{tenant_id}:folder_assignments", new)
        return json_response(self, {"ok": True})
