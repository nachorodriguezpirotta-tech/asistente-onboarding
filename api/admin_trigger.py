"""
POST /api/admin_trigger?t=<admin_token>
Body: { id: <pedido_id> }

Dispara manualmente el workflow de GitHub Actions para provisionar un pedido.
Útil cuando el auto-trigger del oauth_callback falló y querés forzarlo.
"""

import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, kv_update, json_response


ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")


def trigger_workflow(pedido_id: str) -> tuple[bool, str]:
    gh_pat = os.environ.get("GITHUB_PAT", "")
    gh_owner = os.environ.get("GITHUB_OWNER", "")
    gh_repo = os.environ.get("GITHUB_REPO", "asistente-onboarding")
    if not gh_pat or not gh_owner:
        return (False, "GITHUB_PAT o GITHUB_OWNER no seteados")
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
            return (r.status == 204, f"status {r.status}")
    except Exception as e:
        return (False, str(e))


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        qs = parse_qs(urlparse(self.path).query)
        token = (qs.get("t", [""])[0]).strip()

        if not ADMIN_TOKEN:
            return json_response(self, {"error": "ADMIN_TOKEN no seteado"}, 500)
        if token != ADMIN_TOKEN:
            return json_response(self, {"error": "Token inválido"}, 403)

        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        pedido_id = body.get("id", "").strip()
        if not pedido_id:
            return json_response(self, {"error": "Falta id"}, 400)

        pedido = kv_get(f"pedido:{pedido_id}")
        if not pedido:
            return json_response(self, {"error": "Pedido no encontrado"}, 404)

        ok, msg = trigger_workflow(pedido_id)
        if ok:
            kv_update(f"pedido:{pedido_id}", {"status": "provisioning"})
            return json_response(self, {"ok": True, "message": "Workflow disparado"})
        return json_response(self, {"ok": False, "error": msg}, 500)
