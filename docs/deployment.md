# Deployment Runbook

## Environment Variables

| Variable | Where Set | Purpose | How to Obtain |
|---|---|---|---|
| `DATABASE_URL` | Railway | PostgreSQL connection string | [Railway Postgres service reference](https://docs.railway.app/guides/postgresql) (automatically injected) |
| `SECRET_KEY` | Railway | Session/token encryption key; ≥32 chars, production startup fails if invalid | Generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `ANTHROPIC_API_KEY` | Railway | Claude API access for advice/digest generation | [Anthropic Console](https://console.anthropic.com/account/keys) |
| `INITIAL_USER_EMAIL` | Railway | Admin account email (seed-only; production refuses `you@example.com`) | Use real email (e.g., `admin@investikid.com`) |
| `INITIAL_USER_PASSWORD` | Railway | Admin account password (seed-only; production refuses `change-me`) | Generate a strong password; recommended 20+ characters |
| `ENV` | Railway | Deployment environment | Set to `production` for prod; gates hardening (cookies, throttling, caps) |
| `GURU_DIGEST_HOUR` | Railway (optional) | Daily digest trigger hour (UTC) | Integer 0–23; defaults to `7` |
| `GURU_TIMEZONE` | Railway (optional) | Scheduler timezone for digest timing | IANA timezone string; defaults to `Europe/London` |
| `DATA_ENCRYPTION_KEY` | Railway (**required in prod**) | Fernet key encrypting holdings/reports/chat/notes at rest; MUST be distinct from `SECRET_KEY` and NOT the committed dev key (prod fails hard on either, in any list position). Accepts a comma-separated `new,old` list for staged rotation (see Key Rotation below). Generate: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`. **Durable secret — back it up; if lost, all encrypted data is unrecoverable.** Must be set before the first deploy that includes migration 0007. | generate + store in a password manager |
| `ADMIN_EMAILS` | Railway (optional) | Comma-separated admin allowlist for the `/admin` area | defaults to the owner email; e.g. `a@x.com,b@x.com` |
| `GURU_DAILY_BUDGET_USD` | Railway (optional) | Per-user daily LLM spend cap (429 `budget_exhausted` over it) | decimal; defaults to `1.00` |
| `ORSO_HSBC_CLIENT_ID` | Railway (optional) | HSBC WMFS widget gateway header | [HSBC devtools](https://www.hsbc.co.uk/about-hsbc/policies-and-practices/digital-policies/developer-programs/) (leave empty if unused) |
| `ORSO_HSBC_CLIENT_SECRET` | Railway (optional) | HSBC WMFS widget gateway header | [HSBC devtools](https://www.hsbc.co.uk/about-hsbc/policies-and-practices/digital-policies/developer-programs/) (leave empty if unused) |

---

## First Deploy

### Step 1: Railway Project + Postgres

1. Create a new [Railway project](https://railway.app)
2. Add a **PostgreSQL** service (Railway will generate `DATABASE_URL`)
3. Add a new **empty** service; connect to this repository

### Step 2: Backend Service from Dockerfile

1. In the empty service, set **root path** to `backend/`
2. Verify the Dockerfile is auto-detected (`backend/Dockerfile`):
   - `FROM python:3.12-slim`
   - `RUN pip install .` (prod deps; dev extras excluded)
   - Non-root user (`appuser`)
   - `EXPOSE 8000`
   - Start: `alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'`
3. Confirm build logs show successful Python 3.12 install and migrations

### Step 3: Set Environment Variables

1. In Railway backend service **Variables**, add:
   - `DATABASE_URL` ← reference the Postgres service (Railway auto-populates)
   - `SECRET_KEY` ← generated value from `python -c "import secrets; print(secrets.token_urlsafe(48))"`
   - `ANTHROPIC_API_KEY` ← from Anthropic Console
   - `INITIAL_USER_EMAIL` ← real admin email
   - `INITIAL_USER_PASSWORD` ← strong password
   - `ENV` ← `production`
   - `GURU_DIGEST_HOUR` ← `7` (or your choice; defaults to 7 if omitted)
   - `GURU_TIMEZONE` ← `Europe/London` (or your choice; defaults if omitted)
   - Optionally: `ORSO_HSBC_CLIENT_ID`, `ORSO_HSBC_CLIENT_SECRET` (leave empty if unused)

### Step 4: Enable "Wait for CI"

1. In Railway backend service **Settings**, enable **"Wait for CI"**
2. Verify GitHub check suite is configured (existing backend + frontend jobs must pass)
3. Note the generated Railway backend domain (e.g., `invest-guru-prod-xyz.up.railway.app`)

### Step 5: Update Vercel Config & Deploy Frontend

1. In `frontend/vercel.json`, replace `RAILWAY_BACKEND_DOMAIN` with the real Railway domain:
   ```json
   { "source": "/api/:path*", "destination": "https://invest-guru-prod-xyz.up.railway.app/api/:path*" }
   ```
2. Commit: `Task 4: railway domain wired to vercel rewrites`
3. Create a new [Vercel project](https://vercel.com/new) with:
   - Root path: `frontend/`
   - Framework: Vite
   - Build command: `npm run build`
   - Output directory: `dist`
4. Deploy on `git push main` (auto-deployment enabled)

### Step 6: Seed the Database

Once backend is running and Postgres is ready:

```bash
cd backend
railway run python -m app.seed
```

**Notes:**
- Idempotent: safe to re-run if needed
- Production mode: refuses to create/keep `you@example.com` or `change-me` defaults
- Creates the initial admin account using `INITIAL_USER_EMAIL` and `INITIAL_USER_PASSWORD`

### Provisioning gotchas (learned 2026-07-09)

- **Railpack doesn't auto-use the Dockerfile.** Railway's default Railpack builder
  will *not* pick up `backend/Dockerfile` on its own — setting the GraphQL
  `dockerfilePath` field alone did not take effect. Explicitly set the service
  variable `RAILWAY_DOCKERFILE_PATH=Dockerfile` so the service actually builds
  from the Dockerfile instead of Railpack's auto-detected build.
- **Pin `PORT=8000`.** If left unset, Railway injects its own `PORT` value at
  runtime, which can mismatch the port the domain is configured to target —
  the domain then 502s even though the container is healthy. Set the service
  variable `PORT=8000` so uvicorn's bind (`--port ${PORT:-8000}`) matches the
  domain's target port.
- **`railway ssh` needs a registered SSH key.** If `railway ssh` fails because
  no SSH key is registered for the account, seed the database from your local
  machine against the Postgres service's `DATABASE_PUBLIC_URL` instead of
  trying to shell into the Railway container. Pull secrets via shell
  substitution so they never get printed or logged:
  ```bash
  cd backend
  DATABASE_URL=$(railway variables --service Postgres --json | jq -r '.DATABASE_PUBLIC_URL') \
    INITIAL_USER_EMAIL=$(railway variables --json | jq -r '.INITIAL_USER_EMAIL') \
    INITIAL_USER_PASSWORD=$(railway variables --json | jq -r '.INITIAL_USER_PASSWORD') \
    ENV=production python -m app.seed
  ```
- **`railway service restart` needs `--yes` non-interactively.** Without it,
  the CLI waits on an interactive confirmation prompt that never arrives in a
  scripted/agent context: `railway service restart --yes`.

---

## Rollback

1. **Backend:** In Railway dashboard, select a previous deployment and click **Redeploy**
2. **Frontend:** In Vercel dashboard, select a previous deployment and click **Rollback**
3. Both rollbacks are instant; previous build starts serving immediately

---

## Database Backups

### Before Risky Migrations

Always backup production before schema changes:

```bash
railway connect postgres
# Then in the psql shell:
\! pg_dump -h $PGHOST -U $PGUSER -d $PGDATABASE > backup_$(date +%Y%m%d_%H%M%S).sql
```

### Automated Backups

Currently: manual only (no automated backup system configured). Consider:
- Railway's built-in backup feature (if available in your plan)
- Daily dump to S3 or another persistent store (out of scope for this phase)

---

## Scheduler (APScheduler)

- **Topology:** Single Railway replica (one process)
- **Digest trigger:** Runs daily at `GURU_DIGEST_HOUR` (default 07:00) in `GURU_TIMEZONE` (default Europe/London)
- **Catch-up on restart:** APScheduler has idempotent catch-up for missed runs; digest is safe to re-trigger if the service restarts
- **Logs:** Check Railway backend logs for digest execution timestamps and any errors
- **Single replica benefit:** No duplicate digest generation from competing instances

---

## Key Rotation

### SECRET_KEY

If a session key is compromised:

1. Generate a new `SECRET_KEY`: `python -c "import secrets; print(secrets.token_urlsafe(48))"`
2. Update Railway backend variable `SECRET_KEY`
3. Redeploy backend service
4. All active sessions become invalid (users must log in again); this is intentional

### ANTHROPIC_API_KEY

1. Rotate the key in [Anthropic Console](https://console.anthropic.com/account/keys)
2. Update Railway backend variable `ANTHROPIC_API_KEY`
3. Redeploy backend service
4. No session impact; chat/digest continues immediately after redeploy

### DATA_ENCRYPTION_KEY (at-rest crypto — the crown jewel)

This key decrypts every encrypted column (holdings, ORSO allocations, Guru
reports, chat, profile/position notes). Losing it makes that data
**unrecoverable**, and a naive one-for-one swap makes all existing ciphertext
undecryptable. Rotation is therefore staged via a **comma-separated key list**
(`new,old`): the **first** key is used to encrypt new writes, and **all** keys
are tried on decrypt, so old ciphertext keeps working until it is re-wrapped.

1. Generate a new key:
   `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
2. Set `DATA_ENCRYPTION_KEY` to **`<new-key>,<old-key>`** (new first) in Railway and redeploy.
   New writes now use the new key; existing rows still decrypt via the old key.
3. Re-wrap existing rows onto the new key (rewrite each encrypted row so it is
   re-encrypted with the new primary). Until a re-wrap migration exists, the
   safe interim state is to **keep the old key in the list** — do not remove it.
4. Once every row is confirmed re-wrapped, set `DATA_ENCRYPTION_KEY` to just
   `<new-key>` and redeploy; retire the old key from your password manager.

Production refuses an empty value, an invalid Fernet key, or the committed dev
key **in any position** of the list (`validate_production_settings`). Keep every
key in the list backed up in a password manager for as long as it appears.

---

## SSE (Server-Sent Events) Verification

**Requirement (§3):** Chat turns must stream token-by-token through the Vercel rewrite proxy.

**Smoke test:**
1. Log in from the public Vercel URL
2. Start a chat turn; verify tokens arrive incremental (not buffered) in the browser console
   - Each event appears with sub-100ms latency
   - Console shows `EventStream` data: events, not a single chunk at the end

**Expected result:** ✓ `verified` — Vercel rewrites pass through SSE streaming without buffering

**Verified 2026-07-09 (production smoke):** a real chat turn through
`https://investment-guru-rose.vercel.app` delivered 5 delta frames spread over
~2.0s (token-by-token, unbuffered) followed by the `done` frame. The fallback
below was NOT needed.

If streaming buffers (all tokens arrive at once):
- **Fallback (contingency, not deployed):** Point only the SSE endpoint at the Railway domain via an absolute URL; first-party cookies still sent (SameSite=Lax + same-origin request); one-route CORS exception required
- **Decision:** Documented here for reference; current design assumes rewrite works

---

## Health Check

**Endpoint:** `GET /api/health`

Use this to confirm the backend is running:

```bash
curl https://RAILWAY_BACKEND_DOMAIN/api/health
```

Expected response: `{"status":"ok"}` (2xx)
