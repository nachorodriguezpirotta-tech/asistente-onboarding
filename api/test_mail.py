"""
POST /api/test_mail?tenant=<id>&t=<token>

Manda al admin_email un mail SIMULANDO una entrega real al cliente.
Sirve para probar:
  - Que el mail llega
  - Cómo se ve en la bandeja
  - El botón "Pedir un cambio" → click → ir al portal del cliente (real)

Requiere que el tenant tenga al menos 1 cliente registrado (con portal_token)
y al menos 1 task. Si no, se crea data dummy temporal.
"""

import hashlib
import hmac
import os
import secrets as _secrets
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, kv_set, json_response, base_url, notify_via_tenant


SECRET = os.environ.get("DASHBOARD_SECRET", "CHANGE_ME")


def verify_token(tenant_id, token):
    expected = hmac.new(SECRET.encode(), tenant_id.encode(), hashlib.sha256).hexdigest()[:24]
    return hmac.compare_digest(expected, token or "")


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
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        tenant_id = self._auth()
        if not tenant_id:
            return json_response(self, {"error": "Token inválido"}, 403)

        tenant = kv_get(f"tenant:{tenant_id}") or {}
        admin_email = (tenant.get("admin_email") or "").strip()
        if not admin_email:
            return json_response(self, {"error": "Configurá tu email primero en Config → Tus notificaciones"}, 400)

        brand = tenant.get("brand_name") or "Crudo"

        # Buscar o crear un cliente con portal_token para el test
        clients = kv_get(f"tenant:{tenant_id}:clients") or []
        test_client = next((c for c in clients if c.get("portal_token") and c.get("email")), None)
        if not test_client:
            # Crear cliente dummy "Cliente de prueba" con portal_token nuevo
            test_client = {
                "id": _secrets.token_urlsafe(6),
                "name": "Cliente de Prueba",
                "email": admin_email,  # va a vos para el test
                "phone": "",
                "notes": "Generado automáticamente por mail de prueba",
                "portal_token": _secrets.token_urlsafe(16),
                "created_at": "2026-01-01T00:00:00Z",
                "_test": True,
            }
            clients.append(test_client)
            kv_set(f"tenant:{tenant_id}:clients", clients)

        # Buscar o crear una task de prueba ASIGNADA a este cliente
        tasks = kv_get(f"tenant:{tenant_id}:tasks") or []
        test_task = next((t for t in tasks
                          if t.get("client", "").lower() == test_client["name"].lower()
                          and t.get("_test")), None)
        if not test_task:
            # Buscar un editor cualquiera
            editors = kv_get(f"tenant:{tenant_id}:editors") or []
            editor_name = editors[0]["name"] if editors else "Editor"
            test_task = {
                "id": _secrets.token_urlsafe(6),
                "client": test_client["name"],
                "title": "Video de Cumpleaños — Test",
                "assignee": editor_name,
                "notes": "",
                "urgent": False,
                "pending_count": 0,
                "status": "done",
                "created_at": "2026-01-01T00:00:00Z",
                "completed_at": "2026-01-01T00:00:00Z",
                "_test": True,
            }
            tasks.append(test_task)
            kv_set(f"tenant:{tenant_id}:tasks", tasks)

        # Armar el mail con el MISMO formato que el real (notify_client_delivery)
        host = self.headers.get("Host", "asistente-onboarding.onrender.com")
        proto = "http" if host.startswith("localhost") else "https"
        portal_link = f"{proto}://{host}/c/{tenant_id}/{test_client['portal_token']}"
        drive_link_dummy = "https://drive.google.com/drive/folders/SAMPLE"

        subject = f"[🧪 PRUEBA] Tu pedido está listo · {brand}"
        text = f"""Hola {test_client["name"]},

⚠️ ESTE ES UN MAIL DE PRUEBA — el flujo real es idéntico.

Tu material está listo. Te lo dejamos en esta carpeta:
{drive_link_dummy}

Si querés pedir un cambio o ver el estado de todos tus pedidos, entrá acá:
{portal_link}

— {brand}
"""
        html = f"""<html><body style="font-family:-apple-system,Segoe UI,sans-serif;max-width:600px;color:#222;line-height:1.6;">
<div style="background:#fef3c7;border:1px solid #f59e0b;border-radius:8px;padding:10px 14px;margin-bottom:18px;color:#78350f;font-size:13px;">
  🧪 <strong>Mail de prueba</strong> — el flujo real es exactamente este. Probá el botón "Pedir un cambio".
</div>
<h2 style="color:#ff6b35;">✅ Tu pedido está listo</h2>
<p>Hola <strong>{test_client["name"]}</strong>,</p>
<p>Tu material está listo. Podés descargarlo desde Drive:</p>
<p style="margin:20px 0;"><a href="{drive_link_dummy}" style="background:#ff6b35;color:white;padding:12px 22px;border-radius:6px;text-decoration:none;font-weight:600;">📁 Ver en Drive</a></p>
<p style="margin:20px 0;"><a href="{portal_link}" style="background:#fff;border:2px solid #ff6b35;color:#ff6b35;padding:12px 22px;border-radius:6px;text-decoration:none;font-weight:600;">📝 Pedir un cambio / Ver mis pedidos</a></p>
<hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
<p style="color:#888;font-size:12px;">— {brand}</p>
</body></html>"""

        sent = notify_via_tenant(tenant_id, admin_email, subject, text, html)
        if not sent:
            return json_response(self, {"error": "No se pudo enviar el mail. Revisá que tu OAuth de Google esté ok."}, 500)

        return json_response(self, {
            "ok": True,
            "sent_to": admin_email,
            "portal_link": portal_link,
            "test_client": test_client["name"],
            "test_task_id": test_task["id"],
        })
