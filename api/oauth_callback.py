"""
GET /api/oauth_callback?code=<code>&state=<pedido_id>

Recibe el callback de Google. Intercambia el code por tokens, guarda en KV,
notifica al admin por mail, y redirige al cliente a /success.html.
"""

from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import (
    kv_get, kv_update, base_url, redirect, json_response,
    google_exchange_code, google_userinfo, notify_admin,
)


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        code = (qs.get("code", [""])[0]).strip()
        state = (qs.get("state", [""])[0]).strip()
        error = qs.get("error", [None])[0]

        if error:
            # Usuario canceló el OAuth
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

        # Verificar identidad
        try:
            userinfo = google_userinfo(tokens["access_token"])
            google_email = userinfo.get("email", "")
        except Exception:
            google_email = ""

        # Guardar tokens en el pedido
        kv_update(f"pedido:{state}", {
            "status": "oauth_done",
            "google_email": google_email,
            "oauth_refresh_token": tokens.get("refresh_token", ""),
            "oauth_access_token": tokens.get("access_token", ""),
            "oauth_expires_in": tokens.get("expires_in", 0),
            "oauth_scope": tokens.get("scope", ""),
            "oauth_received_at": __import__("datetime").datetime.utcnow().isoformat() + "Z",
        })

        # Notificar al admin
        subject = f"🆕 Pedido nuevo: {pedido.get('brand_name')}"
        text = f"""Nuevo cliente pidió implementación.

ID: {state}
Negocio: {pedido.get('brand_name')}
Tipo: {pedido.get('preset')}
Email admin del cliente: {pedido.get('admin_email')}
Cuenta Google conectada: {google_email}
Drive folder: {pedido.get('drive_url')}

Para implementar, corré en tu compu:
    python3 provision.py {state}

(tarda ~15-30 min, después le manda mail al cliente con su URL)
"""
        notify_admin(subject=subject, body_text=text)

        # Redirigir a pantalla de éxito
        return redirect(self, f"/success.html?id={state}")
