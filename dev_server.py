#!/usr/bin/env python3
"""
Dev server local — simula Vercel para testing sin deploy.

  - Sirve /public/* como estáticos
  - Rutea /api/* a las funciones Python en /api/
  - Storage in-memory (dict) en vez de Vercel KV
  - Mock de Google OAuth (no abre Google real, crea pedido fake completo)

Uso:
    python3 dev_server.py
    open http://localhost:3000

Variables de entorno opcionales:
    PORT=3000              (default 3000)
    REAL_GOOGLE=1          (si se setea, usa Google OAuth real)
    GOOGLE_CLIENT_ID=...
    GOOGLE_CLIENT_SECRET=...
"""

import http.server
import importlib.util
import json
import os
import re
import socketserver
import sys
import time
import urllib.parse
from io import BytesIO
from pathlib import Path

BASE = Path(__file__).parent
PORT = int(os.environ.get("PORT", "3000"))
REAL_GOOGLE = os.environ.get("REAL_GOOGLE") == "1"

# ─── Inyectar mocks de _shared antes de que los handlers lo importen ──────────

# Asegurar que api/ esté en sys.path
sys.path.insert(0, str(BASE / "api"))

# Importar el _shared REAL ANTES de reemplazarlo en sys.modules
# (sino el mock se llama a sí mismo recursivamente)
import importlib.util as _ilu
_real_shared_spec = _ilu.spec_from_file_location("_shared_real", BASE / "api" / "_shared.py")
_real_shared = _ilu.module_from_spec(_real_shared_spec)
_real_shared_spec.loader.exec_module(_real_shared)

# Crear módulo _shared MOCK
import types

# Storage persistente en disco para sobrevivir reinicios del dev_server
_KV_FILE = BASE / ".dev_kv.json"
_kv_memory: dict = {}
_logs: list = []


def _kv_load():
    global _kv_memory
    if _KV_FILE.exists():
        try:
            _kv_memory = json.loads(_KV_FILE.read_text())
        except Exception:
            _kv_memory = {}


def _kv_save():
    try:
        _KV_FILE.write_text(json.dumps(_kv_memory))
    except Exception:
        pass


_kv_load()


def _mock_kv_set(key, value, ttl_seconds=None):
    # IMPORTANTE: serializar SIEMPRE — listas, dicts, lo que sea. Sino al
    # persistir en .dev_kv.json falla y se pierde el dato.
    if not isinstance(value, str):
        value = json.dumps(value)
    _kv_memory[key] = {"value": value, "ts": time.time()}
    _kv_save()


def _mock_kv_get(key):
    rec = _kv_memory.get(key)
    if not rec:
        return None
    try:
        return json.loads(rec["value"])
    except Exception:
        return rec["value"]


def _mock_kv_update(key, updates):
    current = _mock_kv_get(key) or {}
    current.update(updates)
    _mock_kv_set(key, current)
    return current


def _mock_kv_request(command_list):
    """Soporta KEYS <pattern>, SET, GET, INCR, EXPIRE para el código que llama _kv_request."""
    cmd = (command_list[0] or "").upper()
    if cmd == "KEYS":
        pattern = command_list[1] if len(command_list) > 1 else "*"
        import fnmatch
        keys = [k for k in _kv_memory.keys() if fnmatch.fnmatch(k, pattern)]
        return {"result": keys}
    if cmd == "GET":
        key = command_list[1]
        rec = _kv_memory.get(key)
        return {"result": rec["value"] if rec else None}
    if cmd == "SET":
        key, value = command_list[1], command_list[2]
        _kv_memory[key] = {"value": value, "ts": time.time()}
        _kv_save()
        return {"result": "OK"}
    if cmd == "INCR":
        key = command_list[1]
        rec = _kv_memory.get(key)
        current = int(rec["value"]) if rec else 0
        new = current + 1
        _kv_memory[key] = {"value": str(new), "ts": time.time()}
        _kv_save()
        return {"result": new}
    if cmd == "EXPIRE":
        return {"result": 1}  # noop en mock
    return {"result": None}


def _mock_new_pedido_id():
    import secrets
    return secrets.token_urlsafe(9)[:12]


def _mock_base_url(handler):
    host = handler.headers.get("Host", f"localhost:{PORT}")
    is_local = host.startswith("localhost") or host.startswith("127.0.0.1")
    return ("http://" if is_local else "https://") + host


def _mock_json_response(handler, data, status=200):
    body = json.dumps(data).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _mock_redirect(handler, url, status=302):
    handler.send_response(status)
    handler.send_header("Location", url)
    handler.send_header("Content-Length", "0")
    handler.end_headers()


def _mock_read_json_body(handler):
    length = int(handler.headers.get("Content-Length", "0"))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw.decode("utf-8"))


def _mock_google_oauth_url(state, redirect_uri):
    if REAL_GOOGLE:
        return _real_shared.google_oauth_url(state, redirect_uri)
    # Mock: redirige directo al callback con un code fake
    return f"{redirect_uri}?code=fake_code_{state}&state={state}"


def _mock_google_exchange_code(code, redirect_uri):
    if REAL_GOOGLE:
        return _real_shared.google_exchange_code(code, redirect_uri)
    # Mock: devuelve tokens fake
    return {
        "access_token": f"fake_access_{code}",
        "refresh_token": f"fake_refresh_{code}",
        "expires_in": 3600,
        "scope": "drive sheets gmail",
    }


def _mock_google_userinfo(access_token):
    if REAL_GOOGLE:
        return _real_shared.google_userinfo(access_token)
    return {"email": "cliente-test@example.com", "name": "Cliente Test"}


def _mock_notify_admin(subject, body_text, body_html=None):
    # Si están seteadas las credenciales reales, mandar mail real
    if os.environ.get("NOTIFY_MAIL_REFRESH_TOKEN") and os.environ.get("ADMIN_NOTIFY_EMAIL"):
        try:
            ok = _real_shared.notify_admin(subject, body_text, body_html)
            if ok:
                print(f"\n📧 [REAL ADMIN MAIL ENVIADO]")
                print(f"   To: {os.environ.get('ADMIN_NOTIFY_EMAIL')}")
                print(f"   Subject: {subject}")
                print()
                return True
        except Exception as e:
            print(f"⚠️  Mail real falló ({e}), cayendo a mock")
    _logs.append({"type": "admin_mail", "subject": subject, "body_text": body_text})
    print(f"\n📧 [MOCK ADMIN MAIL]")
    print(f"   Subject: {subject}")
    print(f"   Body:\n{body_text[:500]}")
    print()
    return True


# Construir el módulo _shared mock
shared_mock = types.ModuleType("_shared")
shared_mock.kv_set = _mock_kv_set
shared_mock.kv_get = _mock_kv_get
shared_mock.kv_update = _mock_kv_update
shared_mock._kv_request = _mock_kv_request
shared_mock.new_pedido_id = _mock_new_pedido_id
shared_mock.base_url = _mock_base_url
shared_mock.json_response = _mock_json_response
shared_mock.redirect = _mock_redirect
shared_mock.read_json_body = _mock_read_json_body
shared_mock.google_oauth_url = _mock_google_oauth_url
shared_mock.google_exchange_code = _mock_google_exchange_code
shared_mock.google_userinfo = _mock_google_userinfo
shared_mock.notify_admin = _mock_notify_admin

sys.modules["_shared"] = shared_mock

# Mock ADMIN_TOKEN env var para admin_get
os.environ.setdefault("ADMIN_TOKEN", "dev_admin_token_local")


# ─── Cargar handlers dinámicamente ─────────────────────────────────────────────

def _load_handler(name):
    """Importa api/<name>.py y devuelve la clase handler."""
    spec = importlib.util.spec_from_file_location(name, BASE / "api" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.handler


# Cache de handlers
_HANDLERS = {}


def get_handler(name):
    if name not in _HANDLERS:
        _HANDLERS[name] = _load_handler(name)
    return _HANDLERS[name]


# ─── Dev HTTP server ──────────────────────────────────────────────────────────

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
    # Multi-tenant API:
    "/api/editors": "editors",
    "/api/tasks": "tasks",
}
# Rutas dinámicas (matchear prefijo):
API_PREFIX_ROUTES = {
    "/api/tenant/": "tenant",
}


class DevHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE), **kwargs)

    def log_message(self, fmt, *args):
        # Quiet
        sys.stderr.write(f"  {self.command} {self.path}\n")

    def _route_api(self, method):
        parsed = urllib.parse.urlparse(self.path)
        # Match exacto
        handler_name = None
        for path, name in API_ROUTES.items():
            if parsed.path == path or parsed.path == path + "/":
                handler_name = name
                break
        # Match por prefijo (rutas con parámetro en URL)
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
                self.send_error(405, "Method Not Allowed")
                return True
        return False

    def do_GET(self):
        if self._route_api("GET"):
            return
        # Rutas dinámicas: /dashboard/<tenant_id> → dashboard.html
        if self.path.startswith("/dashboard/"):
            self.path = "/dashboard.html"
            return super().do_GET()
        # Reescrituras del vercel.json
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


def main():
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), DevHandler) as httpd:
        print(f"\n{'='*60}")
        print(f"  ASISTENTE ONBOARDING — Dev Server")
        print(f"{'='*60}")
        print(f"  URL:     http://localhost:{PORT}")
        print(f"  Mode:    {'REAL Google OAuth' if REAL_GOOGLE else 'MOCK (no Google real)'}")
        print(f"  Storage: in-memory (se pierde al reiniciar)")
        print(f"  Admin token: dev_admin_token_local")
        print(f"{'='*60}")
        print(f"  Ctrl+C para detener\n")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n  Server detenido.")


if __name__ == "__main__":
    main()
