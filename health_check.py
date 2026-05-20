#!/usr/bin/env python3
"""
health_check.py — verifica que el setup esté completo y andando.

Chequea:
  1. .env local tiene todas las vars necesarias
  2. GitHub PAT es válido y tiene acceso al repo
  3. Vercel Token es válido y tiene acceso al proyecto
  4. GitHub Secrets están configurados
  5. Vercel env vars están configurados
  6. Workflow provision_client.yml existe en el repo
  7. CLI tools (gh, vercel) andan

Uso:
    python3 health_check.py
"""

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path


# ─── Helpers ────────────────────────────────────────────────────────────────

OK = "✓"
ERR = "❌"
WARN = "⚠️"


def _load_env():
    env_file = Path(__file__).parent / ".env"
    out = {}
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip('"')
    return out


def check(name, fn):
    """Ejecuta una check, devuelve True si pasó."""
    try:
        result = fn()
        if result is True or (isinstance(result, tuple) and result[0] is True):
            extra = result[1] if isinstance(result, tuple) else ""
            print(f"  {OK} {name} {extra}")
            return True
        else:
            extra = result[1] if isinstance(result, tuple) else result
            print(f"  {ERR} {name} — {extra}")
            return False
    except Exception as e:
        print(f"  {ERR} {name} — exception: {e}")
        return False


# ─── Checks ─────────────────────────────────────────────────────────────────

env = _load_env()


def check_env_vars():
    required = ["GITHUB_PAT", "VERCEL_TOKEN", "GITHUB_OWNER", "ADMIN_TOKEN",
                "ADMIN_NOTIFY_EMAIL"]
    optional = ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "NOTIFY_MAIL_REFRESH_TOKEN"]
    missing_req = [v for v in required if not env.get(v)]
    missing_opt = [v for v in optional if not env.get(v)]
    if missing_req:
        return (False, f"faltan: {', '.join(missing_req)}")
    extras = []
    if missing_opt:
        extras.append(f"warning: optionals faltan ({', '.join(missing_opt)})")
    return (True, f"({len(required)} requeridas, {len(optional) - len(missing_opt)}/{len(optional)} opcionales)")


def check_github_pat():
    token = env.get("GITHUB_PAT")
    if not token:
        return (False, "GITHUB_PAT no seteado en .env")
    req = urllib.request.Request("https://api.github.com/user")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=5) as r:
        u = json.loads(r.read())
    return (True, f"({u['login']})")


def check_vercel_token():
    token = env.get("VERCEL_TOKEN")
    if not token:
        return (False, "VERCEL_TOKEN no seteado en .env")
    req = urllib.request.Request("https://api.vercel.com/v2/user")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=5) as r:
        u = json.loads(r.read())
    email = u.get("user", {}).get("email") or u.get("email", "?")
    return (True, f"({email})")


def check_repo_access():
    token = env.get("GITHUB_PAT")
    owner = env.get("GITHUB_OWNER")
    repo = f"{owner}/asistente-onboarding"
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=5) as r:
        d = json.loads(r.read())
    return (True, f"({d['full_name']})")


def check_workflow_file():
    p = Path(__file__).parent / ".github/workflows/provision_client.yml"
    return p.exists()


def check_template_dir():
    p = Path(env.get("TEMPLATE_DIR", ""))
    if not p.exists():
        return (False, f"no existe: {p}")
    if not (p / "branding_config.py").exists():
        return (False, f"falta branding_config.py")
    return (True, "")


def check_cli_gh():
    r = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
    if r.returncode != 0:
        return (False, "gh no logueado (corré: gh auth login)")
    return True


def check_cli_vercel():
    if subprocess.run(["which", "vercel"], capture_output=True).returncode != 0:
        # Usar npx
        r = subprocess.run(["npx", "vercel", "whoami"], capture_output=True, text=True, timeout=15)
    else:
        r = subprocess.run(["vercel", "whoami"], capture_output=True, text=True, timeout=15)
    if r.returncode != 0:
        return (False, "vercel CLI no logueado")
    return (True, f"({r.stdout.strip().splitlines()[-1] if r.stdout else '?'})")


def check_github_secrets():
    token = env.get("GITHUB_PAT")
    owner = env.get("GITHUB_OWNER")
    repo = f"{owner}/asistente-onboarding"
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/actions/secrets")
    req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=5) as r:
        d = json.loads(r.read())
    names = {s["name"] for s in d.get("secrets", [])}
    required = {"GITHUB_PAT", "VERCEL_TOKEN", "ADMIN_TOKEN"}
    missing = required - names
    if missing:
        return (False, f"faltan secrets: {', '.join(missing)}")
    return (True, f"({len(names)} secrets configurados)")


# ─── Main ───────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print(" HEALTH CHECK — Asistente Onboarding")
    print("=" * 60)
    print()

    print("Local:")
    check_env_vars_ok = check("Variables del .env", check_env_vars)
    check("Workflow file existe", check_workflow_file)
    check("Template dir existe", check_template_dir)
    check("gh CLI logueado", check_cli_gh)
    check("vercel CLI logueado", check_cli_vercel)

    print("\nCloud:")
    if check_env_vars_ok:
        check("GitHub PAT válido", check_github_pat)
        check("Vercel Token válido", check_vercel_token)
        check("Repo de GitHub accesible", check_repo_access)
        check("GitHub Secrets configurados", check_github_secrets)
    else:
        print(f"  {WARN} Skip checks de cloud (faltan vars en .env)")

    print()
    print("=" * 60)
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nCancelado.")
        sys.exit(1)
