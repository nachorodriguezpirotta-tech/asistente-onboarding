# Asistente — Onboarding self-service

Landing pública + wizard de OAuth + script de provisionamiento.
Convierte el template (`asistente-template/`) en un producto vendible
sin que vos toques nada por cliente nuevo.

---

## Flow para el cliente

```
1. Entra a tuapp.vercel.app                     [landing]
2. Click "Empezar"
3. Llena form (nombre, mail, tipo de negocio,   [/start]
   y Drive: una carpeta o "Toda mi unidad")
4. Click "Conectar Google" → autoriza permisos  [Google OAuth]
5. Ve pantalla "Tu sistema se está armando"     [/success]
   con barra de progreso que se actualiza sola
6. En 5-30 min recibe mail con su URL
7. Entra → wizard "primera vez" → carga su equipo
8. Sistema andando
```

## Flow para vos (Ignacio)

```
Te llega mail "🆕 Pedido nuevo: <cliente>"
   ↓
En tu compu:
   python3 provision.py <pedido_id>
   ↓
El script (5-15 min):
   - Descarga datos + tokens del cliente
   - Copia el template
   - Aplica preset + branding
   - Crea repo GitHub privado
   - Setea GitHub Secrets
   - Crea proyecto Vercel + env vars
   - Deploya
   - Manda mail welcome al cliente
   ↓
Cliente recibe mail con su URL → entra → carga equipo → listo
```

---

## Setup inicial (1 vez)

### 1. Crear OAuth Client en Google Cloud

`console.cloud.google.com → APIs & Services → Credentials → + CREATE → OAuth client ID`

- Type: **Web application**
- Authorized redirect URIs:
  ```
  https://<TU-ONBOARDING>.vercel.app/api/oauth_callback
  http://localhost:3000/api/oauth_callback
  ```

Habilitá estas APIs en el mismo proyecto:
- Google Drive API
- Google Sheets API
- Gmail API

Copiá Client ID + Client Secret.

### 2. Deploy del onboarding

```bash
cd asistente-onboarding
vercel link            # o: npx vercel link
vercel env add GOOGLE_CLIENT_ID
vercel env add GOOGLE_CLIENT_SECRET
vercel env add ADMIN_TOKEN          # random largo, generá con: openssl rand -base64 32
vercel env add ADMIN_NOTIFY_EMAIL   # tu mail
vercel env add NOTIFY_MAIL_REFRESH_TOKEN
vercel env add NOTIFY_MAIL_CLIENT_ID
vercel env add NOTIFY_MAIL_CLIENT_SECRET
```

### 3. Conectar Vercel KV (la "DB")

En el dashboard de Vercel del proyecto:
- **Storage** → **Create Database** → **KV** → click
- `KV_REST_API_URL` y `KV_REST_API_TOKEN` se setean automático

### 4. Deploy

```bash
vercel --prod
```

### 5. Tu `.env` local (para provision.py)

```bash
cp .env.example .env
# Llenar:
#   ONBOARDING_URL=https://<tu-onboarding>.vercel.app
#   ONBOARDING_ADMIN_TOKEN=<mismo ADMIN_TOKEN del server>
#   TEMPLATE_DIR=/path/al/asistente-template
#   GITHUB_OWNER=tu-user-github
```

### 6. Instalar tools locales

```bash
brew install gh && gh auth login

# Vercel: o lo instalás global con sudo npm i -g vercel,
# o usás npx vercel (provision.py detecta cuál tenés)
```

---

## Probar local sin deploy

Hay un dev server que simula Vercel completo con mocks de Google:

```bash
python3 dev_server.py
# → http://localhost:3000
```

- Storage in-memory (se borra al reiniciar)
- OAuth con Google es MOCK (no abre Google real, devuelve tokens fake)
- Mail al admin es MOCK (no manda, imprime en consola)

Para usar Google REAL en local:
```bash
REAL_GOOGLE=1 GOOGLE_CLIENT_ID=... GOOGLE_CLIENT_SECRET=... python3 dev_server.py
```

## Provisionar un cliente (dry-run primero!)

```bash
# Probar sin tocar nada
python3 provision.py <pedido_id> --dry-run

# Real
python3 provision.py <pedido_id>
```

---

## Archivos

```
asistente-onboarding/
├── public/                  # Static (servido por Vercel)
│   ├── index.html           # Landing pública
│   ├── start.html           # Wizard form + OAuth
│   └── success.html         # Post-OAuth con polling de status
│
├── api/                     # Vercel serverless (Python)
│   ├── _shared.py           # KV + OAuth + mail helpers
│   ├── start.py             # POST: crea pedido
│   ├── oauth_init.py        # GET: redirige a Google
│   ├── oauth_callback.py    # GET: recibe code, guarda tokens, notifica
│   ├── status.py            # GET: público, cliente ve progreso
│   └── admin_get.py         # GET/POST: vos (provision.py)
│
├── dev_server.py            # Server local para testing sin deploy
├── provision.py             # Script local: provisiona un cliente
├── vercel.json
├── requirements.txt
└── .env.example
```

## Endpoints del backend

| Endpoint | Método | Quién | Descripción |
|---|---|---|---|
| `/api/start` | POST | Cliente | Crea pedido nuevo (sin tokens todavía) |
| `/api/oauth_init?id=X` | GET | Cliente | Redirige a Google OAuth |
| `/api/oauth_callback?code=X&state=Y` | GET | Google | Recibe tokens, notifica admin |
| `/api/status?id=X` | GET | Cliente | Progreso público (sin tokens) |
| `/api/admin_get?id=X&t=TOKEN` | GET | Vos | Datos completos del pedido (con tokens) |
| `/api/admin_get?t=TOKEN` | POST | Vos | Update status (provision.py) |

## Costos mensuales

| Servicio | Tier | Costo |
|---|---|---|
| Vercel hosting | Hobby | $0 |
| Vercel KV | Hobby (30k req/mes) | $0 |
| Google OAuth | Free | $0 |
| Google Drive/Sheets/Gmail API | Free | $0 |
| **Total** | | **$0** |
