"""
GET /api/oauth_callback?code=<code>&state=<pedido_id>

Recibe el callback de Google. Intercambia el code por tokens, guarda en KV,
notifica al admin por mail, y redirige al cliente a /success.html.
"""

from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

import os
import json
import urllib.request

from _shared import (
    kv_get, kv_update, base_url, redirect, json_response,
    google_exchange_code, google_userinfo, notify_admin,
)


def trigger_github_workflow(pedido_id: str) -> bool:
    """Dispara el workflow `provision_client.yml` para que GitHub Actions
    deploye este pedido automáticamente. Devuelve True si se disparó OK."""
    gh_pat = os.environ.get("GITHUB_PAT", "")
    gh_owner = os.environ.get("GITHUB_OWNER", "")
    gh_repo = os.environ.get("GITHUB_REPO", "asistente-onboarding")
    if not gh_pat or not gh_owner:
        print(f"⚠️  GITHUB_PAT o GITHUB_OWNER no seteados — workflow no se dispara")
        return False
    url = f"https://api.github.com/repos/{gh_owner}/{gh_repo}/actions/workflows/provision_client.yml/dispatches"
    body = json.dumps({
        "ref": "main",
        "inputs": {"pedido_id": pedido_id},
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Authorization", f"Bearer {gh_pat}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status == 204
    except Exception as e:
        print(f"❌ workflow_dispatch falló: {e}")
        return False


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

        # Track oauth completion
        try:
            import datetime as _dt
            from _shared import _kv_request as _kvr
            today = _dt.datetime.utcnow().strftime("%Y-%m-%d")
            _kvr(["INCR", f"track:{today}:oauth_completed"])
        except Exception:
            pass

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

        # ── AUTO-DEPLOY ─────────────────────────────────────────────────
        # Disparar el workflow de GitHub Actions que va a deployar este cliente
        # automáticamente. Si falla, fallback: notificar al admin para que lo
        # haga manual.
        workflow_ok = trigger_github_workflow(state)

        if workflow_ok:
            # Marcar como "en cola de provisionamiento"
            kv_update(f"pedido:{state}", {"status": "provisioning"})
            print(f"✅ Workflow disparado para pedido {state}")
        else:
            # Fallback: notificar al admin
            subject = f"🆕 Pedido nuevo (manual): {pedido.get('brand_name')}"
            text = f"""El auto-deploy no se pudo disparar — provisionalo manual.

ID: {state}
Negocio: {pedido.get('brand_name')}
Tipo: {pedido.get('preset')}
Email cliente: {pedido.get('admin_email')}
Google conectada: {google_email}

Comando: python3 provision.py {state}
"""
            notify_admin(subject=subject, body_text=text)

        # Redirigir a pantalla de éxito (el polling muestra el estado real)
        return redirect(self, f"/success.html?id={state}")
