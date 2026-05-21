#!/usr/bin/env python3
"""
prod_server.py — servidor de producción para Render/Railway/Fly.

Diferencias vs dev_server.py:
  - Usa puerto de la env var PORT (no hardcoded 3000)
  - Storage: KV via API HTTP (Upstash o Vercel KV — ambos compatibles)
  - OAuth Google REAL (sin mocks)
  - Mail real al admin
  - Logging más detallado

Uso en Render:
    Build:  pip install -r requirements.txt
    Start:  python3 prod_server.py
"""

import http.server
import importlib.util
import json
import os
import socketserver
import sys
import urllib.parse
from pathlib import Path

BASE = Path(__file__).parent
PORT = int(os.environ.get("PORT", "10000"))

sys.path.insert(0, str(BASE / "api"))


# ─── Cargar handlers dinámicamente ─────────────────────────────────────────────

def _load_handler(name):
    spec = importlib.util.spec_from_file_location(name, BASE / "api" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.handler


_HANDLERS = {}


def get_handler(name):
    if name not in _HANDLERS:
        _HANDLERS[name] = _load_handler(name)
    return _HANDLERS[name]


# ─── HTTP server ──────────────────────────────────────────────────────────────

API_ROUTES = {
    "/api/start": "start",
    "/api/oauth_init": "oauth_init",
    "/api/oauth_callback": "oauth_callback",
    "/api/admin_get": "admin_get",
    "/api/admin_list": "admin_list",
    "/api/admin_trigger": "admin_trigger",
    "/api/admin_stats": "admin_stats",
    "/api/status": "status",
    "/api/lookup": "lookup",
    "/api/waitlist": "waitlist",
    "/api/track": "track",
    "/api/editors": "editors",
    "/api/tasks": "tasks",
    "/api/drive_folders": "drive_folders",
    "/api/folder_assignments": "folder_assignments",
    "/api/tenant_stats": "tenant_stats",
    "/api/aliases": "aliases",
    "/api/drive_folder_info": "drive_folder_info",
    "/api/scan_drive": "scan_drive",
    # Nuevas APIs (timeline, comentarios, clientes, portal, IA chat, weekly digest, revisiones)
    "/api/timeline": "timeline",
    "/api/comments": "comments",
    "/api/clients": "clients",
    "/api/client_portal": "client_portal",
    "/api/chat": "chat",
    "/api/weekly_digest": "weekly_digest",
    "/api/revisions": "revisions",
    "/api/test_mail": "test_mail",
    "/api/remind_editor": "remind_editor",
}
API_PREFIX_ROUTES = {
    "/api/tenant/": "tenant",
}


class ProdHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE), **kwargs)

    def log_message(self, fmt, *args):
        sys.stderr.write(f"{self.address_string()} - {self.command} {self.path}\n")

    def _route_api(self, method):
        parsed = urllib.parse.urlparse(self.path)
        handler_name = None
        for path, name in API_ROUTES.items():
            if parsed.path == path or parsed.path == path + "/":
                handler_name = name
                break
        if not handler_name:
            for prefix, name in API_PREFIX_ROUTES.items():
                if parsed.path.startswith(prefix):
                    handler_name = name
                    break
        if handler_name:
            HandlerCls = get_handler(handler_name)
            inst = HandlerCls.__new__(HandlerCls)
            inst.rfile = self.rfile
            inst.wfile = self.wfile
            inst.headers = self.headers
            inst.command = self.command
            inst.path = self.path
            inst.client_address = self.client_address
            inst.request = self.request
            inst.server = self.server
            inst.send_response = self.send_response
            inst.send_header = self.send_header
            inst.end_headers = self.end_headers
            inst.send_error = self.send_error
            method_name = f"do_{method}"
            if hasattr(inst, method_name):
                return getattr(inst, method_name)()
            else:
                self.send_error(405)
                return True
        return False

    def do_GET(self):
        if self._route_api("GET"):
            return
        # Ruta dinámica /dashboard/<tenant_id> → dashboard.html
        if self.path.startswith("/dashboard/"):
            self.path = "/dashboard.html"
            return super().do_GET()
        # Ruta dinámica /c/<tenant_id>/<client_token> → client.html (portal del cliente)
        if self.path.startswith("/c/"):
            self.path = "/client.html"
            return super().do_GET()
        mapping = {
            "/": "/index.html",
            "/start": "/start.html",
            "/success": "/success.html",
            "/admin": "/admin.html",
            "/privacy": "/privacy.html",
            "/terms": "/terms.html",
            "/mi-pedido": "/mi-pedido.html",
            "/pricing": "/pricing.html",
            "/about": "/about.html",
        }
        if self.path in mapping:
            self.path = mapping[self.path]
        # 404 personalizado
        return super().do_GET()

    def do_POST(self):
        if self._route_api("POST"):
            return
        self.send_error(404)

    def do_PATCH(self):
        if self._route_api("PATCH"):
            return
        self.send_error(404)

    def do_DELETE(self):
        if self._route_api("DELETE"):
            return
        self.send_error(404)

    def do_OPTIONS(self):
        if self._route_api("OPTIONS"):
            return
        self.send_error(404)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    """Maneja múltiples conexiones en paralelo."""
    allow_reuse_address = True
    daemon_threads = True


def main():
    httpd = ThreadedHTTPServer(("0.0.0.0", PORT), ProdHandler)
    print(f"\n{'='*60}")
    print(f"  ASISTENTE ONBOARDING — Production Server")
    print(f"{'='*60}")
    print(f"  Listening on port: {PORT}")
    print(f"  Storage backend: {'Upstash/Vercel KV (REST)' if os.environ.get('KV_REST_API_URL') else 'NO KV (will fail!)'}")
    print(f"  OAuth: {'REAL Google' if os.environ.get('GOOGLE_CLIENT_ID') else 'NOT CONFIGURED'}")
    print(f"{'='*60}\n", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Server stopped.")


if __name__ == "__main__":
    main()
