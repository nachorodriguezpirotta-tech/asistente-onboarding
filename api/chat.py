"""
POST /api/chat?tenant=<id>&t=<token>  body: {question}
  → manda la pregunta + contexto del tenant a Gemini 2.5 Flash
  → devuelve {"answer": "..."}

Requiere env var GEMINI_API_KEY.
"""

import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, json_response, read_json_body


SECRET = os.environ.get("DASHBOARD_SECRET", "CHANGE_ME")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"


SYSTEM_PROMPT = (
    "Sos un asistente que responde preguntas sobre la operación de una agencia creativa. "
    "Te paso los datos actuales del sistema. Respondé conciso, en español argentino informal, "
    "con números concretos cuando los tengas. Si no podés responder con la data, decilo."
)


def verify_token(tenant_id, token):
    expected = hmac.new(SECRET.encode(), tenant_id.encode(), hashlib.sha256).hexdigest()[:24]
    return hmac.compare_digest(expected, token or "")


def _truncate(s: str, max_chars: int) -> str:
    if not s:
        return ""
    s = str(s)
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + "…"


def build_context(tenant_id: str) -> str:
    """Arma un dump compacto de toda la data del tenant para inyectar en el prompt."""
    tenant = kv_get(f"tenant:{tenant_id}") or {}
    tasks = kv_get(f"tenant:{tenant_id}:tasks") or []
    editors = kv_get(f"tenant:{tenant_id}:editors") or []
    clients = kv_get(f"tenant:{tenant_id}:clients") or []
    timeline = kv_get(f"tenant:{tenant_id}:timeline") or []
    folder_assignments = kv_get(f"tenant:{tenant_id}:folder_assignments") or []

    # Brand
    brand = tenant.get("brand_name") or tenant.get("business_name") or "(sin nombre)"

    # Tasks: dejamos solo los campos relevantes
    tasks_compact = []
    for t in tasks:
        tasks_compact.append({
            "id": t.get("id"),
            "client": t.get("client"),
            "title": _truncate(t.get("title", ""), 120),
            "assignee": t.get("assignee"),
            "status": t.get("status"),
            "urgent": t.get("urgent", False),
            "pending_count": t.get("pending_count", 0),
            "notes": _truncate(t.get("notes", ""), 200),
            "created_at": t.get("created_at"),
            "completed_at": t.get("completed_at"),
        })

    editors_compact = [
        {"id": e.get("id"), "name": e.get("name"), "email": e.get("email")}
        for e in editors
    ]

    clients_compact = []
    for c in clients:
        clients_compact.append({
            "id": c.get("id"),
            "name": c.get("name"),
            "email": c.get("email"),
            "editor": c.get("editor") or c.get("assigned_editor"),
            "created_at": c.get("created_at"),
        })

    timeline_compact = []
    for ev in timeline[:50]:
        timeline_compact.append({
            "ts": ev.get("ts"),
            "type": ev.get("type"),
            "client": ev.get("client"),
            "actor": ev.get("actor"),
            "payload": ev.get("payload") or {},
        })

    folder_compact = []
    for fa in folder_assignments:
        folder_compact.append({
            "folder_id": fa.get("folder_id"),
            "folder_name": fa.get("folder_name"),
            "editor_id": fa.get("editor_id"),
            "editor_name": fa.get("editor_name"),
        })

    parts = [
        f"AGENCIA: {brand}",
        f"TENANT_ID: {tenant_id}",
        "",
        f"EDITORES ({len(editors_compact)}):",
        json.dumps(editors_compact, ensure_ascii=False),
        "",
        f"CLIENTES ({len(clients_compact)}):",
        json.dumps(clients_compact, ensure_ascii=False),
        "",
        f"TAREAS ({len(tasks_compact)}):",
        json.dumps(tasks_compact, ensure_ascii=False),
        "",
        f"ASIGNACIONES DE CARPETAS ({len(folder_compact)}):",
        json.dumps(folder_compact, ensure_ascii=False),
        "",
        f"TIMELINE (últimos {len(timeline_compact)} eventos, más recientes primero):",
        json.dumps(timeline_compact, ensure_ascii=False),
    ]
    ctx = "\n".join(parts)

    # ~3000 tokens ≈ 12000 chars (rule of thumb 4 chars/token).
    max_chars = 12000
    if len(ctx) > max_chars:
        ctx = ctx[:max_chars] + "\n…[contexto truncado]"
    return ctx


def call_gemini(prompt: str) -> str:
    """Llama a Gemini 2.5 Flash REST API y devuelve el texto. Tira excepción si falla."""
    body = {
        "contents": [
            {"role": "user", "parts": [{"text": prompt}]}
        ]
    }
    url = f"{GEMINI_URL}?key={GEMINI_API_KEY}"
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
    )
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())

    try:
        candidates = data.get("candidates") or []
        if not candidates:
            return "(Gemini no devolvió respuesta)"
        parts = candidates[0].get("content", {}).get("parts", []) or []
        text = "".join(p.get("text", "") for p in parts).strip()
        return text or "(respuesta vacía)"
    except Exception:
        return "(no pude parsear la respuesta de Gemini)"


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

        if not GEMINI_API_KEY:
            return json_response(self, {"error": "Chat IA no configurado"}, 500)

        try:
            data = read_json_body(self)
        except Exception:
            return json_response(self, {"error": "JSON inválido"}, 400)

        question = (data.get("question") or "").strip()
        if not question:
            return json_response(self, {"error": "Falta question"}, 400)

        try:
            context = build_context(tenant_id)
        except Exception as e:
            return json_response(self, {"error": f"No pude cargar el contexto: {e}"}, 500)

        full_prompt = (
            f"{SYSTEM_PROMPT}\n\n"
            f"=== DATOS DEL SISTEMA ===\n{context}\n=== FIN DATOS ===\n\n"
            f"PREGUNTA DEL USUARIO:\n{question}"
        )

        try:
            answer = call_gemini(full_prompt)
        except urllib.error.HTTPError as e:
            try:
                err_body = e.read().decode("utf-8", errors="replace")
            except Exception:
                err_body = ""
            return json_response(self, {"error": f"Gemini HTTP {e.code}: {err_body[:300]}"}, 500)
        except Exception as e:
            return json_response(self, {"error": f"Gemini falló: {e}"}, 500)

        return json_response(self, {"answer": answer})
