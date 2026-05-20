"""
POST /api/scan_drive?tenant=<id>&t=<token>

Escanea las carpetas vinculadas del tenant. Para cada archivo NUEVO en alguna
carpeta vinculada, crea una task asignada al editor de esa carpeta.

Para evitar duplicados: mantiene `tenant:<id>:known_files` con los file IDs ya
procesados. Solo crea tasks para archivos nuevos.

Si el archivo es muy viejo (createdTime > 7 días) se considera baseline → se
marca como conocido sin crear task (evita falsos positivos del primer scan).

Esto se llama:
  - Manualmente desde el dashboard (botón "Scan ahora")
  - Periódicamente desde el cron en GitHub Actions
"""

import base64
import datetime
import hashlib
import hmac
import json
import os
import secrets as _secrets
import urllib.error
import urllib.parse
import urllib.request
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

from _shared import kv_get, kv_set, json_response, timeline_add


SECRET = os.environ.get("DASHBOARD_SECRET", "CHANGE_ME")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")

# Extensiones que cuentan como "trabajo nuevo" — el resto se ignora
INPUT_EXTS = {
    # Video
    ".mp4", ".mov", ".avi", ".mkv", ".m4v", ".webm", ".mxf",
    # Foto / RAW
    ".jpg", ".jpeg", ".png", ".heic", ".cr2", ".cr3", ".arw", ".nef", ".dng", ".raf", ".orf",
    # Audio
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".aiff",
    # Doc
    ".pdf", ".doc", ".docx", ".xlsx", ".csv",
}

BASELINE_AGE_DAYS = 7


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


def list_files_in_folder(access_token: str, folder_id: str):
    """Lista archivos (no carpetas) directos en la carpeta dada. Incluye nested? No por ahora."""
    q = f"'{folder_id}' in parents and mimeType != 'application/vnd.google-apps.folder' and trashed=false"
    params = {
        "q": q,
        "fields": "files(id,name,mimeType,size,createdTime,modifiedTime),nextPageToken",
        "pageSize": "1000",
        "orderBy": "createdTime desc",
        "supportsAllDrives": "true",
        "includeItemsFromAllDrives": "true",
    }
    url = "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {access_token}")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read()).get("files", [])


def has_valid_extension(filename: str) -> bool:
    name_lower = filename.lower()
    for ext in INPUT_EXTS:
        if name_lower.endswith(ext):
            return True
    return False


def is_too_old(created_time_iso: str) -> bool:
    try:
        ts = created_time_iso.replace("Z", "+00:00")
        # Sacar microsegundos si tiene
        if "." in ts:
            base, _, rest = ts.partition(".")
            tz = rest.split("+", 1)[1] if "+" in rest else rest.split("-", 1)[1]
            ts = base + ("+" + tz if "+" in rest else "-" + tz)
        dt = datetime.datetime.fromisoformat(ts)
        age = datetime.datetime.now(datetime.timezone.utc) - dt
        return age.days > BASELINE_AGE_DAYS
    except Exception:
        return False


def now_iso():
    return datetime.datetime.utcnow().isoformat() + "Z"


def send_mail_from_tenant(access_token: str, to: str, from_name: str, subject: str, text: str, html: str = None) -> bool:
    """Envía mail desde la cuenta Google del tenant via Gmail API."""
    if not to:
        return False
    msg = MIMEMultipart("alternative")
    msg["To"] = to
    msg["Subject"] = subject
    msg["From"] = from_name
    msg.attach(MIMEText(text, "plain", "utf-8"))
    if html:
        msg.attach(MIMEText(html, "html", "utf-8"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
    try:
        req = urllib.request.Request(
            "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            data=json.dumps({"raw": raw}).encode(),
            method="POST",
        )
        req.add_header("Authorization", f"Bearer {access_token}")
        req.add_header("Content-Type", "application/json")
        urllib.request.urlopen(req, timeout=10)
        return True
    except Exception as e:
        print(f"⚠️  Send mail failed: {e}")
        return False


def notify_client_delivery(tenant: dict, client_record: dict, task: dict, access_token: str, output_folder_id: str = ""):
    """Manda mail al cliente final avisando que su pedido está listo."""
    to_email = (client_record.get("email") or "").strip()
    if not to_email:
        return False
    brand = tenant.get("brand_name") or tenant.get("business_name") or "Equipo"
    client_name = client_record.get("name", "")
    drive_link = f"https://drive.google.com/drive/folders/{output_folder_id}" if output_folder_id else ""
    portal_token = client_record.get("portal_token", "")
    portal_link = ""
    tenant_id = tenant.get("id") or tenant.get("tenant_id") or ""
    if portal_token and tenant_id:
        portal_link = f"/c/{tenant_id}/{portal_token}"  # solo path — el cliente lo abre desde mail con base URL del mismo dominio (mejor armar absoluto si lo tenemos)

    subject = f"✅ Tu pedido está listo · {brand}"
    text = f"""Hola {client_name},

Tu material está listo. Te lo dejamos en esta carpeta:
{drive_link}

Si querés pedir un cambio o ver el estado de todos tus pedidos, entrá acá:
{portal_link}

— {brand}
"""
    html = f"""<html><body style="font-family:-apple-system,Segoe UI,sans-serif;max-width:600px;color:#222;line-height:1.6;">
<h2 style="color:#ff6b35;">✅ Tu pedido está listo</h2>
<p>Hola <strong>{client_name}</strong>,</p>
<p>Tu material está listo. Podés descargarlo desde Drive:</p>
{f'<p style="margin:20px 0;"><a href="{drive_link}" style="background:#ff6b35;color:white;padding:12px 22px;border-radius:6px;text-decoration:none;font-weight:600;">📁 Ver en Drive</a></p>' if drive_link else ''}
{f'<p style="margin:20px 0;"><a href="{portal_link}" style="color:#ff6b35;">Ver todos tus pedidos / pedir cambios →</a></p>' if portal_link else ''}
<hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
<p style="color:#888;font-size:12px;">— {brand}</p>
</body></html>"""
    return send_mail_from_tenant(access_token, to_email, brand, subject, text, html)


def find_client_record(tenant_id: str, client_name: str) -> dict:
    """Busca un registro de cliente por nombre fuzzy (substring case-insensitive)."""
    clients = kv_get(f"tenant:{tenant_id}:clients") or []
    cn = (client_name or "").lower().strip()
    if not cn:
        return None
    # exact match primero
    for c in clients:
        if c.get("name", "").lower() == cn:
            return c
    # substring
    for c in clients:
        nm = c.get("name", "").lower()
        if nm and (cn in nm or nm in cn):
            return c
    return None


def notify_editor_new_task(tenant: dict, editor: dict, task: dict, access_token: str, folder_id: str):
    """Manda mail al editor avisando que tiene tarea nueva."""
    editor_email = editor.get("email", "").strip()
    if not editor_email:
        return False  # editor sin email — skip

    brand = tenant.get("brand_name", "Asistente")
    drive_link = f"https://drive.google.com/drive/folders/{folder_id}" if folder_id else ""
    count = task.get("pending_count", 1)
    output_singular = tenant.get("output_singular", "tarea")
    output_plural = tenant.get("output_plural", "tareas")
    cliente = task["client"]
    count_label = f"{count} {output_singular if count == 1 else output_plural}"

    subject = f"🎬 Tenés trabajo nuevo de {cliente} — {count_label}"
    text = f"""Hola {editor.get('name')},

Te asignaron trabajo nuevo de {cliente}:
{count_label} nuevo{'s' if count != 1 else ''} en Drive.

Carpeta en Drive:
{drive_link}

— {brand}
"""
    html = f"""<html><body style="font-family:-apple-system,Segoe UI,sans-serif;max-width:600px;color:#222;line-height:1.6;">
<h2 style="color:#ff6b35;">🎬 Trabajo nuevo de {cliente}</h2>
<p>Hola <strong>{editor.get('name')}</strong>,</p>
<p>Te asignaron trabajo nuevo: <strong>{count_label}</strong> nuevo{'s' if count != 1 else ''} en Drive.</p>
{f'<p style="margin:20px 0;"><a href="{drive_link}" style="background:#ff6b35;color:white;padding:12px 22px;border-radius:6px;text-decoration:none;font-weight:600;">📁 Abrir carpeta en Drive</a></p>' if drive_link else ''}
<hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
<p style="color:#888;font-size:12px;">— {brand}</p>
</body></html>"""

    return send_mail_from_tenant(access_token, editor_email, brand, subject, text, html)


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.end_headers()

    def do_POST(self):
        self._scan()

    def do_GET(self):
        self._scan()

    def _scan(self):
        qs = parse_qs(urlparse(self.path).query)
        tenant_id = (qs.get("tenant", [""])[0]).strip()
        token = (qs.get("t", [""])[0]).strip()

        if not verify_token(tenant_id, token):
            return json_response(self, {"error": "Token inválido"}, 403)

        tenant = kv_get(f"tenant:{tenant_id}")
        if not tenant:
            return json_response(self, {"error": "Tenant no encontrado"}, 404)
        refresh = tenant.get("oauth_refresh_token", "")
        if not refresh:
            return json_response(self, {"error": "Sin OAuth tokens"}, 400)

        assignments = kv_get(f"tenant:{tenant_id}:folder_assignments") or []
        if not assignments:
            return json_response(self, {"ok": True, "message": "Sin carpetas vinculadas",
                                       "new_tasks": 0, "scanned_folders": 0})

        # Aliases para resolver nombres
        aliases_raw = kv_get(f"tenant:{tenant_id}:aliases") or []
        aliases_map = {a["nickname"].lower(): a["real_name"] for a in aliases_raw}

        known_files = set(kv_get(f"tenant:{tenant_id}:known_files") or [])
        tasks = kv_get(f"tenant:{tenant_id}:tasks") or []
        editors = kv_get(f"tenant:{tenant_id}:editors") or []
        editors_by_name = {e["name"]: e for e in editors}

        try:
            access_token = refresh_access_token(refresh)
        except Exception as e:
            return json_response(self, {"error": f"OAuth refresh falló: {e}"}, 500)

        new_tasks_count = 0
        errors = []
        scanned = 0

        completed_count = 0

        for assignment in assignments:
            folder_id = assignment["folder_id"]
            folder_name = assignment["folder_name"]
            editor_name = assignment["editor_name"]
            output_folder_id = assignment.get("output_folder_id")
            output_folder_name = assignment.get("output_folder_name", "")

            # Resolver alias del nombre de carpeta si aplica
            real_client_name = aliases_map.get(folder_name.lower(), folder_name)

            # ── 1. Escanear CRUDOS (carpeta input) ──
            try:
                input_files = list_files_in_folder(access_token, folder_id)
            except urllib.error.HTTPError as e:
                errors.append(f"{folder_name}: HTTP {e.code}")
                continue
            except Exception as e:
                errors.append(f"{folder_name}: {e}")
                continue

            scanned += 1

            # Buscar task activa de este cliente+editor
            existing_task = None
            for t in tasks:
                if (t.get("client", "").lower() == real_client_name.lower()
                        and t.get("assignee") == editor_name
                        and t.get("status") != "done"):
                    existing_task = t
                    break

            new_inputs = 0
            for f in input_files:
                if f["id"] in known_files:
                    continue
                if not has_valid_extension(f["name"]):
                    known_files.add(f["id"])
                    continue
                known_files.add(f["id"])
                new_inputs += 1

            # ── 2. Escanear EDITADOS (carpeta output, si existe) ──
            new_outputs = 0
            if output_folder_id:
                try:
                    output_files = list_files_in_folder(access_token, output_folder_id)
                    scanned += 1
                    for f in output_files:
                        if f["id"] in known_files:
                            continue
                        if not has_valid_extension(f["name"]):
                            known_files.add(f["id"])
                            continue
                        known_files.add(f["id"])
                        new_outputs += 1
                except Exception as e:
                    errors.append(f"{output_folder_name}: {e}")

            # ── Lógica de creación / decremento ──
            if new_inputs == 0 and new_outputs == 0:
                continue

            # Si hay crudos nuevos → crear/sumar task
            if new_inputs > 0:
                if existing_task:
                    existing_task["pending_count"] = (existing_task.get("pending_count") or 1) + new_inputs
                    task_to_notify = existing_task
                    is_new_task = False
                else:
                    task_to_notify = {
                        "id": _secrets.token_urlsafe(6),
                        "client": real_client_name,
                        "title": real_client_name,
                        "assignee": editor_name,
                        "notes": "",  # SIN nota automática — el cliente la agrega si quiere
                        "urgent": False,
                        "pending_count": new_inputs,
                        "status": "pending",
                        "created_at": now_iso(),
                        "auto_created": True,
                        "source_folder_id": folder_id,
                    }
                    tasks.append(task_to_notify)
                    new_tasks_count += 1
                    is_new_task = True
                    existing_task = task_to_notify

                # Mandar mail al editor (solo si hay crudos nuevos)
                editor_obj = editors_by_name.get(editor_name)
                if editor_obj and editor_obj.get("email") and not editor_obj.get("on_vacation"):
                    try:
                        notify_editor_new_task(tenant, editor_obj, task_to_notify, access_token, folder_id)
                    except Exception as e:
                        errors.append(f"mail a {editor_name}: {e}")

                # Timeline: archivo(s) subido(s) por el cliente
                try:
                    timeline_add(tenant_id, "file_uploaded", client=real_client_name,
                                 actor="cliente",
                                 payload={"count": new_inputs, "task_id": task_to_notify["id"],
                                          "editor": editor_name, "is_new_task": is_new_task})
                except Exception:
                    pass

            # Si hay editados nuevos → reducir count
            if new_outputs > 0 and existing_task:
                current = existing_task.get("pending_count") or 1
                new_count = max(0, current - new_outputs)
                if new_count == 0:
                    existing_task["status"] = "done"
                    existing_task["completed_at"] = now_iso()
                    existing_task["pending_count"] = 0
                    completed_count += 1
                    # Timeline: entrega cerrada
                    try:
                        timeline_add(tenant_id, "task_done", client=real_client_name,
                                     actor=editor_name,
                                     payload={"task_id": existing_task["id"], "auto": True})
                    except Exception:
                        pass
                    # Mail al cliente final si tenemos su email
                    try:
                        client_record = find_client_record(tenant_id, real_client_name)
                        if client_record and client_record.get("email"):
                            # Asegurar que tenant tenga su id para armar portal link
                            if "id" not in tenant:
                                tenant["id"] = tenant_id
                            notify_client_delivery(tenant, client_record, existing_task, access_token, output_folder_id)
                    except Exception as e:
                        errors.append(f"mail cliente {real_client_name}: {e}")
                else:
                    existing_task["pending_count"] = new_count

                # Timeline: archivo entregado
                try:
                    timeline_add(tenant_id, "file_delivered", client=real_client_name,
                                 actor=editor_name,
                                 payload={"count": new_outputs, "task_id": existing_task["id"],
                                          "remaining": new_count})
                except Exception:
                    pass

        # Guardar
        kv_set(f"tenant:{tenant_id}:known_files", list(known_files))
        kv_set(f"tenant:{tenant_id}:tasks", tasks)

        return json_response(self, {
            "ok": True,
            "new_tasks": new_tasks_count,
            "completed_tasks": completed_count,
            "scanned_folders": scanned,
            "total_known_files": len(known_files),
            "errors": errors,
        })
