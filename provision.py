#!/usr/bin/env python3
"""
provision.py — script local que ejecutás vos cuando llega un pedido nuevo.

Uso:
    python3 provision.py <pedido_id>

Hace todo automático:
  1. Descarga datos del pedido del onboarding backend
  2. Copia el template asistente-template
  3. Aplica preset + branding del cliente
  4. Guarda tokens OAuth del cliente como token.json
  5. Crea repo en GitHub (gh CLI)
  6. Crea proyecto Vercel + setea env vars (vercel CLI)
  7. Deploya
  8. Manda mail al cliente con la URL

Requisitos en tu compu:
  - gh CLI (brew install gh) + gh auth login
  - vercel CLI (npm i -g vercel) + vercel login
  - Variables de entorno (en tu .env local):
      ONBOARDING_URL=https://tuapp.vercel.app
      ONBOARDING_ADMIN_TOKEN=<el mismo que el server>
      TEMPLATE_DIR=/Users/ignacio/Documents/Claude/asistente-template
      GITHUB_OWNER=ignacio  (tu github user)
"""

import os
import sys
import json
import time
import secrets
import shutil
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path


# ─── Config (leer del .env local) ─────────────────────────────────────────────

def _load_env():
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"'))

_load_env()

ONBOARDING_URL = os.environ.get("ONBOARDING_URL", "").rstrip("/")
ADMIN_TOKEN = os.environ.get("ONBOARDING_ADMIN_TOKEN", "")
TEMPLATE_DIR = os.environ.get("TEMPLATE_DIR", str(Path.home() / "Documents/Claude/asistente-template"))
GITHUB_OWNER = os.environ.get("GITHUB_OWNER", "")
CLIENTS_BASE_DIR = os.environ.get(
    "CLIENTS_BASE_DIR",
    str(Path.home() / "Documents/Claude/clients"),
)


# ─── Helpers ────────────────────────────────────────────────────────────────

def step(num, total, msg):
    print(f"\n[{num}/{total}] {msg}")
    print("-" * 60)


def run(cmd, cwd=None, env=None, check=True, capture=False):
    """Run shell command."""
    print(f"  $ {cmd if isinstance(cmd, str) else ' '.join(cmd)}")
    result = subprocess.run(
        cmd if isinstance(cmd, list) else cmd.split(),
        cwd=cwd, env={**os.environ, **(env or {})},
        capture_output=capture, text=True,
    )
    if check and result.returncode != 0:
        print(f"  ❌ FAILED (exit {result.returncode})")
        if capture:
            print(f"  stderr: {result.stderr}")
        sys.exit(1)
    return result


def fetch_pedido(pedido_id: str) -> dict:
    url = f"{ONBOARDING_URL}/api/admin_get?id={pedido_id}&t={urllib.parse.quote(ADMIN_TOKEN)}"
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.loads(r.read())


def update_pedido(pedido_id: str, updates: dict):
    url = f"{ONBOARDING_URL}/api/admin_get?t={urllib.parse.quote(ADMIN_TOKEN)}"
    body = json.dumps({"id": pedido_id, **updates}).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    urllib.request.urlopen(req, timeout=10)


def load_preset(preset_name: str) -> dict:
    """Lee presets/<name>.env del template."""
    path = Path(TEMPLATE_DIR) / "presets" / f"{preset_name}.env"
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"')
    return out


# ─── Main flow ──────────────────────────────────────────────────────────────

def preflight_checks():
    """Chequea que el entorno esté OK antes de empezar."""
    issues = []
    if not ONBOARDING_URL:
        issues.append("ONBOARDING_URL no seteado en .env local")
    if not ADMIN_TOKEN:
        issues.append("ONBOARDING_ADMIN_TOKEN no seteado en .env local")
    if not GITHUB_OWNER:
        issues.append("GITHUB_OWNER no seteado en .env local")
    if not Path(TEMPLATE_DIR).exists():
        issues.append(f"TEMPLATE_DIR no existe: {TEMPLATE_DIR}")
    # gh CLI logueado?
    r = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if r.returncode != 0:
        issues.append("gh CLI no logueado. Corré: gh auth login")
    # vercel CLI / npx vercel disponible?
    r = subprocess.run(["which", "vercel"], capture_output=True, text=True)
    npx_r = subprocess.run(["which", "npx"], capture_output=True, text=True)
    if r.returncode != 0 and npx_r.returncode != 0:
        issues.append("Vercel CLI no disponible. Instalá con: npm i -g vercel  (o usá npx vercel)")
    return issues


def main():
    # Parse args
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    args = [a for a in args if not a.startswith("--")]

    if not args:
        print("Uso: python3 provision.py <pedido_id> [--dry-run]")
        print()
        print("  --dry-run   Solo descarga datos y muestra qué haría, sin tocar nada.")
        sys.exit(1)

    pedido_id = args[0]

    # Preflight
    issues = preflight_checks()
    if issues:
        print("❌ Problemas detectados antes de arrancar:")
        for i in issues:
            print(f"   • {i}")
        sys.exit(1)

    if dry_run:
        print("🧪 DRY-RUN MODE — no se hacen cambios reales")
    print()
    TOTAL = 8

    # ─── 1. Descargar datos del pedido ──────────────────────────────────────
    step(1, TOTAL, "Descargando datos del pedido")
    pedido = fetch_pedido(pedido_id)
    print(f"  Cliente: {pedido['brand_name']}")
    print(f"  Email: {pedido['admin_email']}")
    print(f"  Preset: {pedido['preset']}")
    print(f"  Google conectada: {pedido.get('google_email', '?')}")

    if not pedido.get("oauth_refresh_token"):
        print("  ❌ El pedido no completó OAuth todavía. Esperá que el cliente termine.")
        sys.exit(1)

    # ─── 2. Crear carpeta del cliente ────────────────────────────────────────
    step(2, TOTAL, "Creando carpeta del cliente")
    # Slug 100% ASCII: sin acentos, sin espacios, solo a-z 0-9 y guiones
    import unicodedata as _ud, re as _re
    raw = pedido["brand_name"].lower().strip()
    raw = _ud.normalize("NFD", raw)
    raw = "".join(c for c in raw if _ud.category(c) != "Mn")  # quitar acentos
    raw = _re.sub(r"[^a-z0-9]+", "-", raw)  # cualquier no-alfanum → guión
    raw = _re.sub(r"-+", "-", raw).strip("-")
    slug = raw[:30].strip("-") or "cliente"
    repo_name = f"asistente-{slug}"
    client_dir = Path(CLIENTS_BASE_DIR) / repo_name

    if client_dir.exists():
        if os.environ.get("NON_INTERACTIVE") == "1":
            print(f"  ⚠️  Ya existe: {client_dir} → sobrescribiendo (NON_INTERACTIVE)")
            shutil.rmtree(client_dir)
        else:
            print(f"  ⚠️  Ya existe: {client_dir}")
            if input("  ¿Sobrescribir? [y/N]: ").lower() != "y":
                sys.exit(0)
            shutil.rmtree(client_dir)

    client_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(TEMPLATE_DIR, client_dir, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pyc", ".git", ".env", "tracker.db*", "token*.json", "client_secrets.json",
    ))
    print(f"  ✓ {client_dir}")

    # ─── 3. Aplicar preset + branding ────────────────────────────────────────
    step(3, TOTAL, "Aplicando preset + branding del cliente")

    if pedido["preset"] == "custom":
        # Cliente describió su propio vocabulario en el wizard
        ci = pedido.get("custom_input", "archivo")
        co = pedido.get("custom_output", "entrega")
        ca = pedido.get("custom_assignee", "responsable")
        preset_vars = {
            "INPUT_SINGULAR": ci,
            "INPUT_PLURAL": ci + "s",
            "OUTPUT_SINGULAR": co,
            "OUTPUT_PLURAL": co + "s",
            "ASSIGNEE_SINGULAR": ca,
            "ASSIGNEE_PLURAL": ca + "es",
            "PROJECT_SINGULAR": "proyecto",
            "PROJECT_PLURAL": "proyectos",
            "INPUT_FOLDER_NAMES": "material,input,entrada",
            "INPUT_EXTS": ".pdf,.doc,.docx,.jpg,.jpeg,.png,.mp4,.mov,.xlsx,.csv",
        }
        print(f"  ✓ Vocabulario custom: {ci} → {co} ({ca})")
    else:
        preset_vars = load_preset(pedido["preset"])
        print(f"  ✓ Preset: {pedido['preset']}")

    dashboard_url = f"https://{repo_name}.vercel.app"
    secret_token = secrets.token_urlsafe(32)

    env_vars = {
        "BRAND_NAME": pedido["brand_name"],
        "BRAND_TAGLINE": f"Sistema de seguimiento — {pedido['brand_name']}",
        "PRIMARY_COLOR": "#1a1a1a",
        "ACCENT_COLOR": "#ff6b35",
        "ADMIN_EMAIL": pedido["admin_email"],
        "MAIL_FROM_NAME": pedido["brand_name"],
        "MAIL_FROM_ADDRESS": pedido.get("google_email", pedido["admin_email"]),
        "DASHBOARD_URL": dashboard_url,
        "GITHUB_OWNER": GITHUB_OWNER,
        "GITHUB_REPO": repo_name,
        "GITHUB_REPO_FULL": f"{GITHUB_OWNER}/{repo_name}",
        "DASHBOARD_SECRET": secret_token,
        "VAPID_SUBJECT": f"mailto:{pedido['admin_email']}",
        # Tokens OAuth del cliente (los va a usar el backend del cliente)
        "OAUTH_REFRESH_TOKEN": pedido["oauth_refresh_token"],
        "OAUTH_CLIENT_ID": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "OAUTH_CLIENT_SECRET": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        "MAIL_OAUTH_REFRESH_TOKEN": pedido["oauth_refresh_token"],
        "MAIL_OAUTH_CLIENT_ID": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "MAIL_OAUTH_CLIENT_SECRET": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
        # Vocabulario del preset
        **preset_vars,
    }

    # .env con quote automático para valores con espacios (compat con `source .env`)
    def _quote_if_needed(v: str) -> str:
        v = str(v)
        if v == "" or all(c.isalnum() or c in "-_.@:/+,#" for c in v):
            return v
        # Escapar comillas dobles dentro del valor
        return '"' + v.replace('"', '\\"') + '"'
    env_lines = [f"{k}={_quote_if_needed(v)}" for k, v in env_vars.items()]
    (client_dir / ".env").write_text("\n".join(env_lines) + "\n")
    print(f"  ✓ .env generado con {len(env_vars)} variables")

    # NOTA: NO escribimos token.json en el filesystem del repo — sería leak si se
    # commitea por error. Los tokens del cliente viven en:
    #   - GitHub Secrets (los workflows los usan)
    #   - Vercel env vars (las API functions los usan)
    # Para que esto ande, mail_client.py / auth.py del template tienen que poder
    # construir credentials desde env vars (OAUTH_REFRESH_TOKEN + OAUTH_CLIENT_*).

    # Reemplazar placeholders en HTMLs
    replacements = {
        "__BRAND_NAME__": pedido["brand_name"],
        "__BRAND_SHORT__": pedido["brand_name"].split()[0],
        "__DASHBOARD_URL__": dashboard_url,
        "__ADMIN_EMAIL__": pedido["admin_email"],
        "__FROM_EMAIL__": pedido.get("google_email", pedido["admin_email"]),
        "__ASSIGNEE_NAME__": preset_vars.get("ASSIGNEE_SINGULAR", "responsable"),
        "__ASSIGNEE_PLURAL__": preset_vars.get("ASSIGNEE_PLURAL", "responsables"),
    }
    for f in list(client_dir.glob("*.html")) + [client_dir / "manifest.json"]:
        if not f.exists():
            continue
        content = f.read_text()
        for k, v in replacements.items():
            content = content.replace(k, v)
        f.write_text(content)
    print(f"  ✓ HTMLs con branding reemplazado")

    # ─── 4. Crear repo en GitHub ─────────────────────────────────────────────
    step(4, TOTAL, "Creando repo en GitHub")
    if dry_run:
        print(f"  [DRY-RUN] git init + push a github.com/{GITHUB_OWNER}/{repo_name}")
    else:
        run(["git", "init", "-b", "main"], cwd=client_dir)
        run(["git", "add", "."], cwd=client_dir)
        run(["git", "commit", "-m", "init: cliente provisionado por provision.py"], cwd=client_dir)
        # Si el repo ya existe, comportamiento depende del modo
        check = subprocess.run(["gh", "repo", "view", f"{GITHUB_OWNER}/{repo_name}"], capture_output=True, text=True)
        if check.returncode == 0:
            print(f"  ⚠️  Repo ya existe: github.com/{GITHUB_OWNER}/{repo_name}")
            if os.environ.get("NON_INTERACTIVE") == "1":
                # En CI: usar un sufijo único en vez de borrar (más seguro)
                import time as _t
                repo_name = f"{repo_name}-{int(_t.time())}"
                dashboard_url = f"https://{repo_name}.vercel.app"
                env_vars["GITHUB_REPO"] = repo_name
                env_vars["GITHUB_REPO_FULL"] = f"{GITHUB_OWNER}/{repo_name}"
                env_vars["DASHBOARD_URL"] = dashboard_url
                print(f"  → Usando nombre único: {repo_name}")
            else:
                ans = input("  ¿Borrar y recrear? [y/N]: ")
                if ans.lower() == "y":
                    run(["gh", "repo", "delete", f"{GITHUB_OWNER}/{repo_name}", "--yes"])
                else:
                    print("  Abortado.")
                    sys.exit(1)
        run(["gh", "repo", "create", f"{GITHUB_OWNER}/{repo_name}",
             "--private", "--source=.", "--push"], cwd=client_dir)
        print(f"  ✓ github.com/{GITHUB_OWNER}/{repo_name}")

    # ─── 5. Setear GitHub Secrets ────────────────────────────────────────────
    step(5, TOTAL, "Seteando GitHub Secrets")
    if dry_run:
        print(f"  [DRY-RUN] {len(env_vars)} secrets a setear en {GITHUB_OWNER}/{repo_name}")
    else:
        for k, v in env_vars.items():
            if not v:
                continue
            proc = subprocess.run(
                ["gh", "secret", "set", k, "--repo", f"{GITHUB_OWNER}/{repo_name}"],
                input=str(v), text=True, capture_output=True, cwd=client_dir,
            )
            if proc.returncode != 0:
                print(f"  ⚠️  {k}: {proc.stderr.strip()}")
            else:
                print(f"  ✓ {k}")

    # ─── 6. Deploy a Vercel ──────────────────────────────────────────────────
    step(6, TOTAL, "Deploy a Vercel")
    vercel_bin = ["vercel"] if shutil.which("vercel") else ["npx", "vercel"]
    # En CI siempre pasamos --token, en local vercel CLI ya está logueado
    vercel_token = os.environ.get("VERCEL_TOKEN", "")
    vercel_cmd = vercel_bin + ([f"--token={vercel_token}"] if vercel_token else [])
    if dry_run:
        print(f"  [DRY-RUN] {' '.join(vercel_bin)} link + env add + --prod")
    else:
        # Vercel link (con retry si falla)
        for attempt in range(3):
            r = subprocess.run(
                vercel_cmd + ["link", "--yes", "--project", repo_name],
                cwd=client_dir, capture_output=True, text=True,
            )
            if r.returncode == 0:
                break
            if "Resource is limited" in r.stderr or "more than 100" in r.stderr:
                print(f"  ⚠️  Vercel rate-limited. Marcando pedido como 'queued_vercel' para retry futuro.")
                update_pedido(pedido_id, {"status": "queued_vercel", "vercel_error": r.stderr[:200]})
                sys.exit(2)  # exit code especial: rate limit, no es fallo permanente
            if attempt < 2:
                print(f"  ⚠️  vercel link falló (attempt {attempt+1}/3), retry en {(attempt+1)*5}s")
                import time as _t; _t.sleep((attempt+1)*5)
            else:
                raise RuntimeError(f"vercel link falló: {r.stderr[:200]}")

        # Setear env vars (silencioso si ya existen)
        for k, v in env_vars.items():
            if not v:
                continue
            subprocess.run(
                vercel_cmd + ["env", "add", k, "production"],
                input=str(v), text=True, capture_output=True, cwd=client_dir,
            )

        # Deploy con retry
        for attempt in range(3):
            r = subprocess.run(
                vercel_cmd + ["--prod", "--yes"],
                cwd=client_dir, capture_output=True, text=True,
            )
            if r.returncode == 0:
                print(f"  ✓ Deployado a {dashboard_url}")
                break
            if "Resource is limited" in r.stderr:
                print(f"  ⚠️  Vercel rate-limited en deploy. Marcando 'queued_vercel'.")
                update_pedido(pedido_id, {"status": "queued_vercel", "vercel_error": r.stderr[:200]})
                sys.exit(2)
            if attempt < 2:
                print(f"  ⚠️  deploy falló (attempt {attempt+1}/3): {r.stderr[:100]}")
                import time as _t; _t.sleep((attempt+1)*10)
            else:
                raise RuntimeError(f"vercel deploy falló: {r.stderr[:200]}")

    # ─── 7. Actualizar pedido (status: deployed) ─────────────────────────────
    step(7, TOTAL, "Actualizando estado del pedido")
    if dry_run:
        print("  [DRY-RUN] update_pedido(status=deployed)")
    else:
        update_pedido(pedido_id, {
            "status": "deployed",
            "dashboard_url": dashboard_url,
            "deployed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
        print("  ✓ Marcado como deployed")

    # ─── 8. Mail al cliente + notificación al admin ──────────────────────────
    step(8, TOTAL, "Notificando al cliente y al admin")
    if dry_run:
        print(f"  [DRY-RUN] Mail welcome a {pedido['admin_email']} con URL {dashboard_url}")
    else:
        # 8a. Mail al cliente con su URL
        try:
            send_client_welcome_mail(
                to=pedido["admin_email"],
                brand_name=pedido["brand_name"],
                dashboard_url=dashboard_url,
                preset=pedido.get("preset", ""),
            )
            print(f"  ✓ Mail welcome enviado a {pedido['admin_email']}")
        except Exception as e:
            print(f"  ⚠️  Mail al cliente no enviado ({e})")

        # 8b. Mail al admin (vos) con el resumen
        admin_email = os.environ.get("ADMIN_NOTIFY_EMAIL")
        if admin_email:
            try:
                send_admin_summary_mail(
                    admin_email=admin_email,
                    brand_name=pedido["brand_name"],
                    client_email=pedido["admin_email"],
                    dashboard_url=dashboard_url,
                    preset=pedido.get("preset", ""),
                    pedido_id=pedido_id,
                )
                print(f"  ✓ Resumen enviado a admin ({admin_email})")
            except Exception as e:
                print(f"  ⚠️  Mail admin no enviado ({e})")

    print("\n" + "=" * 60)
    if dry_run:
        print(f"🧪 DRY-RUN COMPLETO. Habría provisionado: {pedido['brand_name']}")
    else:
        print(f"✅ CLIENTE LISTO: {pedido['brand_name']}")
    print(f"   Dashboard: {dashboard_url}")
    print("=" * 60)


def send_admin_summary_mail(admin_email: str, brand_name: str, client_email: str,
                              dashboard_url: str, preset: str, pedido_id: str):
    """Manda mail al admin (vos) con resumen del cliente recién provisionado."""
    import base64, json
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    ADMIN_REFRESH = os.environ.get("NOTIFY_MAIL_REFRESH_TOKEN", "")
    ADMIN_CID = os.environ.get("NOTIFY_MAIL_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID", "")
    ADMIN_CSEC = os.environ.get("NOTIFY_MAIL_CLIENT_SECRET") or os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not (ADMIN_REFRESH and ADMIN_CID and ADMIN_CSEC):
        raise RuntimeError("Faltan NOTIFY_MAIL_* para mandar el resumen al admin")

    data = urllib.parse.urlencode({
        "client_id": ADMIN_CID, "client_secret": ADMIN_CSEC,
        "refresh_token": ADMIN_REFRESH, "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        access_token = json.loads(r.read())["access_token"]

    subject = f"✅ Cliente provisionado: {brand_name}"
    text = f"""Nuevo cliente deployed automáticamente.

Cliente: {brand_name}
Tipo: {preset}
Email: {client_email}
Dashboard: {dashboard_url}
ID del pedido: {pedido_id}

Ya recibió su mail con el link. Cuando entre va a ver el welcome wizard.
"""
    html = f"""<html><body style="font-family:-apple-system,Segoe UI,sans-serif;max-width:600px;color:#222;">
<h2 style="color:#4caf50;">✅ Cliente nuevo deployed</h2>
<p><strong>{brand_name}</strong> ({preset}) acaba de ser provisionado automáticamente.</p>
<table style="border-collapse:collapse;margin:16px 0;">
  <tr><td style="padding:4px 12px 4px 0;color:#666;">Email cliente:</td><td><a href="mailto:{client_email}">{client_email}</a></td></tr>
  <tr><td style="padding:4px 12px 4px 0;color:#666;">Dashboard:</td><td><a href="{dashboard_url}">{dashboard_url}</a></td></tr>
  <tr><td style="padding:4px 12px 4px 0;color:#666;">ID:</td><td><code>{pedido_id}</code></td></tr>
</table>
<p style="color:#666;font-size:13px;">El cliente ya recibió su mail con el link. Cuando entre, va a ver el welcome wizard donde carga su equipo.</p>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["To"] = admin_email
    msg["Subject"] = subject
    msg["From"] = "Asistente Onboarding"
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

    req = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=json.dumps({"raw": raw}).encode(), method="POST",
    )
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")
    urllib.request.urlopen(req, timeout=10)


def send_client_welcome_mail(to: str, brand_name: str, dashboard_url: str, preset: str = ""):
    """Manda mail al cliente con el link de su dashboard ya andando."""
    # Reutilizamos OAuth del admin (env vars del .env local)
    import base64, json
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart

    ADMIN_REFRESH = os.environ.get("NOTIFY_MAIL_REFRESH_TOKEN", "")
    ADMIN_CID = os.environ.get("NOTIFY_MAIL_CLIENT_ID") or os.environ.get("GOOGLE_CLIENT_ID", "")
    ADMIN_CSEC = os.environ.get("NOTIFY_MAIL_CLIENT_SECRET") or os.environ.get("GOOGLE_CLIENT_SECRET", "")
    if not (ADMIN_REFRESH and ADMIN_CID and ADMIN_CSEC):
        raise RuntimeError("Faltan NOTIFY_MAIL_* en .env local para mandar el mail welcome")

    # Refresh token → access token
    data = urllib.parse.urlencode({
        "client_id": ADMIN_CID,
        "client_secret": ADMIN_CSEC,
        "refresh_token": ADMIN_REFRESH,
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        access_token = json.loads(r.read())["access_token"]

    subject = f"🎉 Tu sistema {brand_name} está listo"
    text = f"""¡Tu sistema está andando!

Entrá a tu dashboard:
{dashboard_url}/welcome

Es la primera vez, así que te lleva a una pantalla de bienvenida donde cargás
tu equipo (2 minutos) y mandás un mail de prueba.

Cualquier duda, respondé este mail.

— Asistente Onboarding
"""
    html = f"""<html><body style="font-family:-apple-system,Segoe UI,sans-serif;max-width:600px;color:#222;line-height:1.6;">
<h2 style="color:#ff6b35;">🎉 Tu sistema está listo</h2>
<p>Entrá a tu dashboard de <strong>{brand_name}</strong>:</p>
<p style="margin:24px 0;"><a href="{dashboard_url}/welcome" style="background:#ff6b35;color:white;padding:14px 28px;border-radius:8px;text-decoration:none;font-weight:600;">Configurar mi sistema →</a></p>
<p>La primera vez te lleva a una pantalla de bienvenida donde cargás tu equipo (2 min)
y mandás un mail de prueba.</p>
<p>Cualquier duda, respondé este mail.</p>
<hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
<p style="color:#888;font-size:12px;">— Asistente Onboarding</p>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["To"] = to
    msg["Subject"] = subject
    msg["From"] = "Asistente Onboarding"
    msg.attach(MIMEText(text, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

    req = urllib.request.Request(
        "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
        data=json.dumps({"raw": raw}).encode(),
        method="POST",
    )
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")
    urllib.request.urlopen(req, timeout=10)


if __name__ == "__main__":
    main()
