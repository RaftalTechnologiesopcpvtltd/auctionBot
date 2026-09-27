# Phase 07.6 — Production Access, HTTPS & Security Verification Audit

**Date**: 2026-09-28  
**Scope**: Production connectivity, Hostinger firewall, HTTPS/SSL, Nginx/Gunicorn integration, Docker health, CI/CD, tenant isolation & IDOR protections, password security hardening, and Telegram webhook reachability.

---

## 1. Environment

- **Production Host**: Ubuntu 24.04 LTS VPS (Hostinger)
- **Primary IPv4**: `72.62.248.151`
- **Configured Domains**:
  - `https://auctionbot.shop` (Apex Domain)
  - `https://admin.auctionbot.shop` (Platform Administration Portal)
  - `https://cyg.auctionbot.shop` (Tenant Subdomain)
  - `https://cyg-malaysia.auctionbot.shop` (Tenant Subdomain)
  - `https://malaysia.auctionbot.shop` (Tenant Subdomain)
- **Publicly Open Ports**:
  - `80/tcp` (HTTP — redirects to HTTPS)
  - `443/tcp` (HTTPS — TLS termination via Nginx)
  - `22/tcp` (SSH — remote administration)
- **Internal Only Services (Strictly Isolated on Docker Network `auctionbot_internal`)**:
  - `5432/tcp` (PostgreSQL 16) — Verified blocked externally.
  - `6379/tcp` (Redis 7) — Verified blocked externally.
  - `8000/tcp` (Gunicorn/Django Web Application) — Bound internally to container network.

---

## 2. Infrastructure Status

| Service Container | Image | Status | Health Check / Network |
| :--- | :--- | :--- | :--- |
| `auctionbot_prod_nginx` | `nginx:1.25-alpine` | Up (`restart: always`) | Listens on `0.0.0.0:80`, `0.0.0.0:443`; proxies to `web:8000` |
| `auctionbot_prod_web` | Custom Dockerfile (`python:3.11-slim`) | Up (`restart: always`) | Gunicorn 4 workers / 2 threads; internal health at `/health/` |
| `auctionbot_prod_worker`| Custom Dockerfile (`python:3.11-slim`) | Up (`restart: always`) | Celery worker connected to `redis:6379/0` |
| `auctionbot_prod_postgres` | `postgres:16-alpine` | Up (`restart: always`) | Healthy (`pg_isready`), persistent volume `postgres_prod_data` |
| `auctionbot_prod_redis` | `redis:7-alpine` | Up (`restart: always`) | Healthy (`redis-cli ping`), persistent volume `redis_prod_data` |
| `auctionbot_prod_certbot` | `certbot/certbot:latest` | Up (`restart: unless-stopped`) | Scheduled renewal against `/var/www/certbot` |

---

## 3. HTTPS & Security Headers

- **Certificate Authority**: Let's Encrypt (Production Multi-Domain SAN Certificate).
- **Subject Alternative Names (SANs)**:
  - `auctionbot.shop`
  - `admin.auctionbot.shop`
  - `cyg.auctionbot.shop`
  - `cyg-malaysia.auctionbot.shop`
  - `malaysia.auctionbot.shop`
- **TLS Configuration**:
  - Protocols: `TLSv1.2 TLSv1.3`
  - Ciphers: `HIGH:!aNULL:!MD5;`
  - Session Caching: `shared:SSL:10m`
- **HTTP -> HTTPS Redirection**:
  - HTTP requests to port 80 receive HTTP 301 Permanent Redirect to `https://$host$request_uri` (excluding ACME challenge directory `/.well-known/acme-challenge/`).
- **Django Security Settings**:
  - `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`
  - `SESSION_COOKIE_SECURE = True`
  - `CSRF_COOKIE_SECURE = True`
  - `SECURE_HSTS_SECONDS = 31536000`
  - `SECURE_HSTS_INCLUDE_SUBDOMAINS = True`
  - `SECURE_CONTENT_TYPE_NOSNIFF = True`

---

## 4. CI/CD Verification

- **CI Pipeline (`.github/workflows/ci.yml`)**:
  - Runs on every push and pull request to branch `main`.
  - Spins up ephemeral PostgreSQL 16 and Redis 7 service containers.
  - Executes Django system check: `python manage.py check`.
  - Verifies migration state: `python manage.py makemigrations --check --dry-run`.
  - Runs automated test suite.
- **Deploy Pipeline (`.github/workflows/deploy.yml`)**:
  - Triggers only after successful completion of CI on `main` (or manual dispatch).
  - Connects securely via SSH to Hostinger VPS using GitHub Secrets (`VPS_SSH_HOST`, `VPS_SSH_USER`, `VPS_SSH_KEY`).
  - Pulls latest commit ref.
  - Automatically kills any stray host development server instances (`pkill -f "manage.py runserver"`).
  - Rebuilds and launches Docker Compose containers.
  - Applies database migrations (`python manage.py migrate --no-input`).
  - Collects static assets (`python manage.py collectstatic --no-input`).
  - Verifies live health endpoint (`https://auctionbot.shop/health/`).
  - Secrets: All sensitive keys (`DJANGO_SECRET_KEY`, `POSTGRES_PASSWORD`, `SSH_KEY`) stored exclusively in GitHub Secrets or server-side `.env`. Zero secrets committed to Git repository.

---

## 5. Security & Isolation

### Tenant Isolation Model
- **Platform Administrator**:
  - Identified by `user.is_superuser=True`.
  - Full platform administration rights.
  - Authorized to switch active tenant context.
- **Tenant Administrator**:
  - Role `TenantRole.TENANT_ADMIN` via `TenantMembership`.
  - Bound to exactly one tenant.
  - Strictly blocked from switching tenant context via session forgery, query parameter (`?tenant=other`), or cross-subdomain requests (returns `403 Forbidden`).
- **Tenant Staff**:
  - Role `TenantRole.TENANT_STAFF` via `TenantMembership`.
  - Bound to exactly one tenant.
  - Cannot switch tenants or perform restricted admin operations (such as financial refunds/reversals or platform settings).

### Cross-Tenant IDOR Testing
Automated tests in `apps/dashboard/tests.py` verify that Tenant A cannot access Tenant B data:
- **Auctions**: Attempting to view, cancel, or end Tenant B's auction returns `404 Not Found`.
- **Listings**: Attempting to view, approve, or reject Tenant B's listing returns `404 Not Found`.
- **Transactions & Ledger**: Attempting to view, refund, or reverse Tenant B's financial transactions returns `404 Not Found`.
- **Telegram Users**: Telegram users belonging to Tenant B are never rendered in Tenant A's dashboard.
- **Financial Accounts & Wallets**: Tenant B's balances and ledger accounts are completely isolated from Tenant A's views.
- **Cross-Subdomain Spoofing**: Sending requests with Tenant B's Host header while authenticated as Tenant A user returns `403 Forbidden`.
- **Query Parameter Spoofing**: Adding `?tenant=tenant-b` to URLs while authenticated as Tenant A user returns `403 Forbidden`.

### Password Security Hardening
- **Plaintext Passwords Eliminated**:
  - Dropped `admin_initial_password` field from the `Tenant` model via migration `0004_remove_tenant_admin_initial_password.py`.
  - Removed plaintext password capture from tenant registration in `apps.tenants.super_admin_views`.
  - Removed "Show All Passwords" button, `data-plain` attributes, and plaintext toggle scripts from `apps/tenants/templates/super_admin/dashboard.html`.
  - Implemented secure password reset modal and API endpoint `super_admin_reset_tenant_password` where administrators set a new password directly hashed via `user.set_password()`.
  - Passwords are never retrievable or exposed in plain text.

### Secret Scan Results
- **Django SECRET_KEY**: NOT FOUND in Git tracking (managed via `.env`).
- **Database Passwords**: NOT FOUND in Git tracking.
- **Telegram Bot Tokens**: NOT FOUND in plaintext in codebase or database dumps; encrypted at rest via AES-128-CBC + HMAC-SHA256 (`token_encrypted`).
- **SSH Credentials / Private Keys**: NOT FOUND in repository.
- **Production `.env`**: Properly listed in `.gitignore` and untracked.

---

## 6. Telegram Webhook Reachability

- **Registered Webhook URL**: `https://auctionbot.shop/telegram/webhook/cyg-malaysia/SELLER/`
- **Live Verification**:
  - `setWebhook` executed successfully with Telegram Bot API (`{"ok": true, "result": true, "description": "Webhook was set"}`).
  - `getWebhookInfo` confirmed:
    - URL: `https://auctionbot.shop/telegram/webhook/cyg-malaysia/SELLER/`
    - `has_custom_certificate`: `false`
    - `pending_update_count`: `0`
    - `max_connections`: `40`
    - `ip_address`: `72.62.248.151`
- **Dispatcher & Security Verification**:
  - Verified `X-Telegram-Bot-Api-Secret-Token` authentication (invalid tokens return HTTP 403).
  - Verified update idempotency via `TelegramUpdateLog` (duplicate updates return HTTP 200 without duplicate execution).
  - Verified `/start` and `/help` command dispatchers respond with tenant-branded context.

---

## 7. Test Results

| Test Module | Coverage Area | Tests Run | Result |
| :--- | :--- | :--- | :--- |
| `apps.dashboard.tests` | Dashboard KPIs, cross-tenant isolation, IDOR, auth decorators | 23 | **23/23 PASSED** |
| `apps.telegram_engine.tests` | Webhook endpoints, dispatcher, idempotency, token crypto, commands | 33 | **33/33 PASSED** |
| `apps.tenants.tests` | Tenant models, membership security, super admin reset, no plaintext passwords | 14 | **14/14 PASSED** |
| `apps.listings.tests` | Listing domain, tenant scoping, state transitions | 5 | **5/5 PASSED** |
| **Total Targeted Suite** | **Isolation, Security, HTTPS, Webhooks, Auth** | **75** | **75/75 PASSED** |

*(Full 140-test suite runs in GitHub Actions CI with PostgreSQL service container for concurrent row-locking verification).*

---

## 8. Remaining Issues

- None blocking Phase 07.6.
- In accordance with Phase 07.6 specifications, complete Telegram buyer/seller multi-step workflows (listing wizard, live bidding buttons, wallet top-up UI) are reserved for Phase 08.
- Legacy project `E:\auctionbots\CYG_Aquatics_Malaysia` was kept strictly read-only; no data migration was performed.
