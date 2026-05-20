#!/usr/bin/env python3
"""
setup_tokens.py — configurador automático.

Tomá los 2 tokens (GitHub PAT + Vercel Token) y este script:
  1. Los guarda en .env local
  2. Los sube a GitHub Secrets del repo onboarding
  3. Los setea como env vars de Vercel (para el backend)

Uso:
    python3 setup_tokens.py --github-pat ghp_xxx --vercel-token xxx

O interactivo:
    python3 setup_tokens.py
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path


def _load_env():
    """Carga .env actual en memoria."""
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


def _save_env(env: dict):
    """Guarda .env preservando comentarios donde existían."""
    env_file = Path(__file__).parent / ".env"
    lines = []
    written_keys = set()
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.strip() and not line.strip().startswith("#") and "=" in line:
                k = line.split("=", 1)[0].strip()
                if k in env:
                    v = env[k]
                    if " " in v and not v.startswith('"'):
                        v = '"' + v + '"'
                    lines.append(f"{k}={v}")
                    written_keys.add(k)
                else:
                    lines.append(line)
            else:
                lines.append(line)
    # Agregar las nuevas
    for k, v in env.items():
        if k not in written_keys:
            if " " in v and not v.startswith('"'):
                v = '"' + v + '"'
            lines.append(f"{k}={v}")
    env_file.write_text("\n".join(lines) + "\n")
    print(f"  ✓ .env actualizado")


def verify_github_pat(token: str) -> "dict|None":
    """Verifica que el PAT funciona. Devuelve info del user si OK."""
    req = urllib.request.Request("https://api.github.com/user")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  ❌ GitHub PAT inválido: {e}")
        return None


def verify_vercel_token(token: str) -> "dict|None":
    """Verifica que el Vercel token funciona."""
    req = urllib.request.Request("https://api.vercel.com/v2/user")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  ❌ Vercel token inválido: {e}")
        return None


def set_github_secret(repo: str, name: str, value: str) -> bool:
    """Setea un secret en el repo via gh CLI (más simple que GitHub API que requiere encryption)."""
    proc = subprocess.run(
        ["gh", "secret", "set", name, "--repo", repo],
        input=value, text=True, capture_output=True,
    )
    if proc.returncode == 0:
        print(f"  ✓ GitHub secret {name}")
        return True
    print(f"  ❌ {name}: {proc.stderr.strip()}")
    return False


def set_vercel_env(token: str, project: str, name: str, value: str) -> bool:
    """Setea/actualiza una env var en Vercel via API."""
    # Primero buscar si ya existe
    list_url = f"https://api.vercel.com/v9/projects/{project}/env"
    req = urllib.request.Request(list_url)
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            envs = json.loads(r.read()).get("envs", [])
    except Exception as e:
        print(f"  ❌ list env failed: {e}")
        return False

    existing_id = next((e["id"] for e in envs if e["key"] == name and "production" in e.get("target", [])), None)

    body = {
        "key": name,
        "value": value,
        "type": "encrypted",
        "target": ["production", "preview", "development"],
    }
    if existing_id:
        # Update
        url = f"https://api.vercel.com/v9/projects/{project}/env/{existing_id}"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="PATCH")
    else:
        # Create
        url = f"https://api.vercel.com/v10/projects/{project}/env"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            print(f"  ✓ Vercel env {name}")
            return True
    except Exception as e:
        print(f"  ❌ {name}: {e}")
        return False


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--github-pat", help="GitHub Personal Access Token (ghp_...)")
    p.add_argument("--vercel-token", help="Vercel API Token")
    p.add_argument("--repo", default="nachorodriguezpirotta-tech/asistente-onboarding")
    p.add_argument("--project", default="asistente-onboarding", help="Vercel project name")
    args = p.parse_args()

    print("=" * 60)
    print(" SETUP TOKENS — Asistente Onboarding")
    print("=" * 60)

    github_pat = args.github_pat or input("\nPegá tu GitHub PAT (ghp_...): ").strip()
    vercel_token = args.vercel_token or input("Pegá tu Vercel Token: ").strip()

    if not github_pat or not vercel_token:
        print("\n❌ Faltan tokens")
        sys.exit(1)

    # ─── Verificación ──────────────────────────────────────────────────
    print("\n[1/4] Verificando tokens…")
    gh_user = verify_github_pat(github_pat)
    vc_user = verify_vercel_token(vercel_token)
    if not gh_user or not vc_user:
        print("\n❌ Tokens inválidos, abortando.")
        sys.exit(1)
    print(f"  ✓ GitHub: {gh_user.get('login')}")
    print(f"  ✓ Vercel: {vc_user.get('user', {}).get('email') or vc_user.get('email', '?')}")

    # ─── Guardar en .env ───────────────────────────────────────────────
    print("\n[2/4] Guardando en .env local…")
    env = _load_env()
    env["GITHUB_PAT"] = github_pat
    env["VERCEL_TOKEN"] = vercel_token
    _save_env(env)

    # ─── Setear en GitHub Secrets ──────────────────────────────────────
    print(f"\n[3/4] Configurando GitHub Secrets en {args.repo}…")
    secrets_to_set = {
        # GitHub no permite secrets que empiecen con GITHUB_, usamos PROVISION_GH_PAT
        "PROVISION_GH_PAT": github_pat,
        "VERCEL_TOKEN": vercel_token,
        "ONBOARDING_URL": env.get("ONBOARDING_URL", "https://asistente-onboarding.vercel.app"),
        "ADMIN_TOKEN": env.get("ADMIN_TOKEN", env.get("ONBOARDING_ADMIN_TOKEN", "")),
        "GOOGLE_CLIENT_ID": env.get("GOOGLE_CLIENT_ID", ""),
        "GOOGLE_CLIENT_SECRET": env.get("GOOGLE_CLIENT_SECRET", ""),
        "NOTIFY_MAIL_REFRESH_TOKEN": env.get("NOTIFY_MAIL_REFRESH_TOKEN", ""),
        "NOTIFY_MAIL_CLIENT_ID": env.get("NOTIFY_MAIL_CLIENT_ID", ""),
        "NOTIFY_MAIL_CLIENT_SECRET": env.get("NOTIFY_MAIL_CLIENT_SECRET", ""),
    }
    for k, v in secrets_to_set.items():
        if v:
            set_github_secret(args.repo, k, v)
        else:
            print(f"  ⏭️  {k}: vacío, skip")

    # ─── Setear en Vercel env vars ─────────────────────────────────────
    print(f"\n[4/4] Configurando Vercel env vars en proyecto '{args.project}'…")
    vercel_envs = {
        "GITHUB_PAT": github_pat,
        "GITHUB_OWNER": args.repo.split("/")[0],
        "GITHUB_REPO": args.repo.split("/")[1],
        "VERCEL_TOKEN": vercel_token,
        "ADMIN_TOKEN": env.get("ADMIN_TOKEN", env.get("ONBOARDING_ADMIN_TOKEN", "")),
        "ADMIN_NOTIFY_EMAIL": env.get("ADMIN_NOTIFY_EMAIL", ""),
        "GOOGLE_CLIENT_ID": env.get("GOOGLE_CLIENT_ID", ""),
        "GOOGLE_CLIENT_SECRET": env.get("GOOGLE_CLIENT_SECRET", ""),
        "NOTIFY_MAIL_REFRESH_TOKEN": env.get("NOTIFY_MAIL_REFRESH_TOKEN", ""),
        "NOTIFY_MAIL_CLIENT_ID": env.get("NOTIFY_MAIL_CLIENT_ID", ""),
        "NOTIFY_MAIL_CLIENT_SECRET": env.get("NOTIFY_MAIL_CLIENT_SECRET", ""),
    }
    for k, v in vercel_envs.items():
        if v:
            set_vercel_env(vercel_token, args.project, k, v)
        else:
            print(f"  ⏭️  {k}: vacío, skip")

    print("\n" + "=" * 60)
    print("✅ SETUP COMPLETO")
    print("=" * 60)
    print(f"""
Próximos pasos:
  1. Cuando Vercel libere el rate limit, hacer push o trigger deploy:
       git commit --allow-empty -m "trigger redeploy"
       git push

  2. Una vez deployado, probar el flow end-to-end:
       - Entrá a la URL pública del onboarding
       - Llená el form
       - Hacé OAuth
       - El sistema debería deployar tu cliente automáticamente en ~5-10 min

  3. Para chequear que todo esté bien configurado:
       python3 health_check.py
""")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nCancelado.")
        sys.exit(1)
