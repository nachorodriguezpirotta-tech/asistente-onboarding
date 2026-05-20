"""
GET /api/oauth_callback?code=<code>&state=<pedido_id>

Recibe el callback de Google. Intercambia el code por tokens, CREA EL TENANT,
y redirige directo al dashboard del cliente con su token.

Es el corazón del modelo Instant SaaS: el cliente termina OAuth y AL TOQUE
tiene su dashboard funcionando, sin esperar deploys ni nada.
"""

import datetime
import hashlib
import hmac
import os
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import (
    kv_get, kv_set, kv_update, base_url, redirect, json_response,
    google_exchange_code, google_userinfo, notify_admin,
)


SECRET = os.environ.get("DASHBOARD_SECRET", "CHANGE_ME")


def make_tenant_token(tenant_id: str) -> str:
    return hmac.new(SECRET.encode(), tenant_id.encode(), hashlib.sha256).hexdigest()[:24]


# Presets pre-armados (vocabulario por defecto según tipo de negocio)
PRESETS = {
    "video_edit":       {"input_singular": "crudo",       "input_plural": "crudos",          "output_singular": "editado",          "output_plural": "editados",          "assignee_singular": "editor",      "assignee_plural": "editores",     "project_singular": "cliente",   "project_plural": "clientes"},
    "photo_studio":     {"input_singular": "shoot",       "input_plural": "shoots",          "output_singular": "foto retocada",    "output_plural": "fotos retocadas",   "assignee_singular": "retocador",   "assignee_plural": "retocadores",  "project_singular": "sesión",    "project_plural": "sesiones"},
    "design_agency":    {"input_singular": "brief",       "input_plural": "briefs",          "output_singular": "diseño",           "output_plural": "diseños",           "assignee_singular": "diseñador",   "assignee_plural": "diseñadores",  "project_singular": "proyecto",  "project_plural": "proyectos"},
    "ugc":              {"input_singular": "material",    "input_plural": "materiales",      "output_singular": "edit",             "output_plural": "edits",             "assignee_singular": "editor",      "assignee_plural": "editores",     "project_singular": "creator",   "project_plural": "creators"},
    "music_production": {"input_singular": "demo",        "input_plural": "demos",           "output_singular": "mezcla",           "output_plural": "mezclas",           "assignee_singular": "productor",   "assignee_plural": "productores",  "project_singular": "track",     "project_plural": "tracks"},
    "events":           {"input_singular": "brief",       "input_plural": "briefs",          "output_singular": "evento producido", "output_plural": "eventos producidos","assignee_singular": "productor",   "assignee_plural": "productores",  "project_singular": "evento",    "project_plural": "eventos"},
    "coaching":         {"input_singular": "sesión",      "input_plural": "sesiones",        "output_singular": "grabación",        "output_plural": "grabaciones",       "assignee_singular": "coach",       "assignee_plural": "coaches",      "project_singular": "alumno",    "project_plural": "alumnos"},
}


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        code = (qs.get("code", [""])[0]).strip()
        state = (qs.get("state", [""])[0]).strip()  # pedido_id (temporal)
        error = qs.get("error", [None])[0]

        if error:
            return redirect(self, f"/start.html?error=denied")
        if not code or not state:
            return json_response(self, {"error": "Faltan code/state"}, 400)

        pedido = kv_get(f"pedido:{state}")
        if not pedido:
            return json_response(self, {"error": "Pedido expirado o inválido"}, 404)

        # Intercambiar code por tokens
        redirect_uri = f"{base_url(self)}/api/oauth_callback"
        try:
            tokens = google_exchange_code(code, redirect_uri)
        except Exception as e:
            return json_response(self, {"error": f"OAuth falló: {e}"}, 500)

        try:
            userinfo = google_userinfo(tokens["access_token"])
            google_email = userinfo.get("email", "")
        except Exception:
            google_email = ""

        # ── CREAR TENANT ────────────────────────────────────────────────
        # El pedido_id se convierte en tenant_id (mismo string)
        tenant_id = state
        preset = pedido.get("preset", "video_edit")
        vocab = PRESETS.get(preset, PRESETS["video_edit"])
        if preset == "custom":
            # Vocabulario que vino del form
            vocab = {
                "input_singular": pedido.get("custom_input", "archivo"),
                "input_plural": pedido.get("custom_input", "archivo") + "s",
                "output_singular": pedido.get("custom_output", "entrega"),
                "output_plural": pedido.get("custom_output", "entrega") + "s",
                "assignee_singular": pedido.get("custom_assignee", "responsable"),
                "assignee_plural": pedido.get("custom_assignee", "responsable") + "es",
                "project_singular": "proyecto",
                "project_plural": "proyectos",
            }

        tenant = {
            "id": tenant_id,
            "brand_name": pedido.get("brand_name", ""),
            "admin_email": pedido.get("admin_email", ""),
            "google_email": google_email,
            "preset": preset,
            "drive_url": pedido.get("drive_url", ""),
            "drive_folder_id": pedido.get("drive_folder_id", ""),
            "drive_mode": pedido.get("drive_mode", "my_drive"),
            "oauth_refresh_token": tokens.get("refresh_token", ""),
            "oauth_access_token": tokens.get("access_token", ""),
            "oauth_scope": tokens.get("scope", ""),
            "primary_color": "#ff6b35",
            "accent_color": "#ff6b35",
            "created_at": datetime.datetime.utcnow().isoformat() + "Z",
            **vocab,
        }
        kv_set(f"tenant:{tenant_id}", tenant)

        # Seed: 3 editores genéricos para que el cliente arranque con algo
        seed_editors = [
            {"id": "ed1", "name": "Editor 1", "email": "", "active": True},
            {"id": "ed2", "name": "Editor 2", "email": "", "active": True},
            {"id": "ed3", "name": "Editor 3", "email": "", "active": True},
        ]
        kv_set(f"tenant:{tenant_id}:editors", seed_editors)
        kv_set(f"tenant:{tenant_id}:tasks", [])
        kv_set(f"tenant:{tenant_id}:clients", [])

        # ── NOTIFICAR AL ADMIN ──────────────────────────────────────────
        try:
            subject = f"🆕 Tenant nuevo: {pedido.get('brand_name')}"
            text = f"""Llegó un cliente nuevo y ya tiene su dashboard activo.

ID: {tenant_id}
Cliente: {pedido.get('brand_name')}
Tipo: {preset}
Email: {pedido.get('admin_email')}
Google conectada: {google_email}

Su dashboard: {base_url(self)}/dashboard/{tenant_id}?t={make_tenant_token(tenant_id)}
"""
            notify_admin(subject=subject, body_text=text)
        except Exception as e:
            print(f"⚠️  notify_admin falló: {e}")

        # ── BORRAR el pedido temporal (ya cumplió su función) ────────────
        # No es estrictamente necesario, pero limpia.
        # kv_set(f"pedido:{state}", None)  # opcional

        # ── REDIRIGIR DIRECTO AL DASHBOARD ──────────────────────────────
        token = make_tenant_token(tenant_id)
        return redirect(self, f"/dashboard/{tenant_id}?t={token}&welcome=1")
