# Phase 07.6 — Discovery & Current Production State (Before)

**Date**: 2026-09-28  
**Scope**: Production connectivity, Hostinger firewall, HTTPS/SSL, Nginx, Gunicorn, Docker health, CI/CD, tenant isolation, authentication, password security, and Telegram webhook reachability.

---

## 1. Current Production Architecture

The production environment is hosted on an Ubuntu VPS (Hostinger) running Docker Compose:
- **Host Public IPv4**: `72.62.248.151`
- **Application Server**: Django 5.x running under Gunicorn (4 worker processes, 2 threads per worker) bound internally to `0.0.0.0:8000`.
- **Background Worker**: Celery worker (`celery -A config worker --concurrency=4`) attached to Redis message broker.
- **Relational Database**: PostgreSQL 16 (Alpine) running in container `auctionbot_prod_postgres`, mapped to persistent volume `postgres_prod_data`.
- **Cache & Message Broker**: Redis 7 (Alpine) running in container `auctionbot_prod_redis`, mapped to persistent volume `redis_prod_data`.
- **Edge Reverse Proxy**: Nginx 1.25 (Alpine) terminating TLS and routing requests to upstream Gunicorn, serving static files directly from `/app/staticfiles/`.
- **SSL Auto-Renewal**: Certbot container executing background renewal checks against `/var/www/certbot`.

---

## 2. Domains, DNS & Ports

### DNS & Domains
- `auctionbot.shop` -> `72.62.248.151` (A Record)
- `admin.auctionbot.shop` -> `72.62.248.151` (A Record)
- `cyg.auctionbot.shop` -> `72.62.248.151` (A Record)
- `cyg-malaysia.auctionbot.shop` -> `72.62.248.151` (A Record)
- `malaysia.auctionbot.shop` -> `72.62.248.151` (A Record)

### Ports
- **Publicly Exposed Ports**:
  - `80/tcp` (HTTP) -> Mapped to Nginx container.
  - `443/tcp` (HTTPS) -> Mapped to Nginx container.
  - `22/tcp` (SSH) -> System remote administration.
- **Internal Only (Isolated on Docker bridge network `auctionbot_internal`)**:
  - `5432/tcp` (PostgreSQL) — NOT exposed to the public Internet.
  - `6379/tcp` (Redis) — NOT exposed to the public Internet.
  - `8000/tcp` (Gunicorn/Web) — NOT exposed to the public Internet.

---

## 3. Current Containers & Docker Compose
- `auctionbot_prod_web`: Gunicorn WSGI server.
- `auctionbot_prod_worker`: Celery asynchronous worker pool.
- `auctionbot_prod_postgres`: PostgreSQL 16 database.
- `auctionbot_prod_redis`: Redis 7 cache/broker.
- `auctionbot_prod_nginx`: Nginx reverse proxy with TLS termination.
- `auctionbot_prod_certbot`: Certbot renewal daemon.

All containers are configured with `restart: always` or `restart: unless-stopped`.

---

## 4. Current CI/CD Workflow
- Workflow files: `.github/workflows/ci.yml` and `.github/workflows/deploy.yml`.
- `ci.yml`: Triggers on push/PR to `main`. Provisions PostgreSQL and Redis service containers, installs development requirements, executes `manage.py check`, verifies migrations are in sync (`makemigrations --check --dry-run`), and runs the Django automated test suite.
- `deploy.yml`: Triggers upon successful completion of `ci.yml` on branch `main` (or via `workflow_dispatch`). Connects via SSH (`appleboy/ssh-action`), pulls target Git ref, builds production containers, handles SSL certification via Certbot, runs database migrations, collects static assets, ensures superadmin account, and executes health check validations.

---

## 5. Current HTTPS Status
- **SSL Certificate**: Let's Encrypt multi-domain certificate covering `auctionbot.shop`, `admin.auctionbot.shop`, `cyg.auctionbot.shop`, `cyg-malaysia.auctionbot.shop`, and `malaysia.auctionbot.shop`.
- **Cipher Suite & Protocols**: TLS 1.2 and TLS 1.3 enforced with modern cipher suites (`HIGH:!aNULL:!MD5`).
- **HTTP -> HTTPS Redirection**: Enforced on port 80 (except for ACME challenge path `/.well-known/acme-challenge/` and internal health monitoring).
- **Security Headers**: HSTS (`Strict-Transport-Security`), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: strict-origin-when-cross-origin`.
- **Django Proxy Headers**: `SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")`, `SESSION_COOKIE_SECURE = True`, `CSRF_COOKIE_SECURE = True`.

---

## 6. Current Firewall Assumptions
- Public traffic is allowed exclusively on ports 80 (HTTP) and 443 (HTTPS), with SSH on port 22.
- Hostinger firewall must ensure that port 5432 (Postgres) and port 6379 (Redis) are dropped from external interfaces.
- Host-level `iptables` / Docker proxy maintains internal isolation for backing services.

---

## 7. Current Telegram Webhook Status
- Webhook endpoint: `POST https://auctionbot.shop/telegram/webhook/<tenant_slug>/<bot_type>/`
- Routing: Handled by `apps.telegram_engine.views.TelegramWebhookView`.
- Security: Header validation against `X-Telegram-Bot-Api-Secret-Token`.
- Idempotency: `TelegramUpdateLog` table tracking `update_id`.
- Bot status: Webhook registered with Telegram API (`getWebhookInfo` confirmed active with `pending_update_count: 0`).

---

## 8. Current Tenant Authorization Model
- Three user levels defined:
  1. `PLATFORM_ADMIN`: Global platform superuser or active `PLATFORM_ADMIN` membership. Can switch tenant context.
  2. `TENANT_ADMIN`: Administrator belonging to a single tenant organization. Cannot switch tenant context.
  3. `TENANT_STAFF`: Staff user belonging to a single tenant organization with operational permissions. Cannot switch tenant context.
- Tenant context resolution: Performed via `apps.tenants.security.resolve_requested_tenant` and `apps.dashboard.decorators.dashboard_auth_required`.

---

## 9. Current Password Handling & Problems Found

### Critical Vulnerability: Plaintext Password Storage & Exposure
- **Finding**: The `Tenant` model in `apps/tenants/models.py` defines `admin_initial_password = models.CharField(max_length=128, blank=True)`.
- **Exposure**:
  - `apps/tenants/super_admin_views.py` stores the plain password string entered during tenant creation into `tenant.admin_initial_password`.
  - `apps/tenants/templates/super_admin/dashboard.html` outputs this plaintext password in HTML attributes (`data-plain="{{ item.tenant.admin_initial_password }}"`) and includes a "Show All Passwords" button.
  - Tests in `apps/tenants/tests.py` assert that passwords remain readable in plaintext.
- **Violation**: Violates Step 10 and core security principles. Passwords must never be stored in plaintext or recoverable by an administrator.

### Tenant Resolution Subdomain Leaks
- **Finding**: In `dashboard_auth_required`, if a tenant admin accesses another tenant's subdomain (e.g., `cyg-other.auctionbot.shop`), `resolve_requested_tenant(user, session_tenant_id)` raises `PermissionDenied`, but the fallback silently switches context back to the user's assigned tenant while keeping the foreign subdomain in the browser URL.
- **Fix Required**: If a non-platform user accesses a foreign tenant subdomain, the request must be explicitly rejected with `HttpResponseForbidden` (403).

### Staff User Tenant Switching Fallback
- **Finding**: In `apps/tenants/security.py`, lines 137-140 grant any `is_staff` user access to the default tenant even if they have no explicit membership.
- **Fix Required**: Strict membership check. A tenant admin or tenant staff without an active membership must be denied.

---

## 10. Summary of Action Items for Phase 07.6
1. **Eliminate Plaintext Passwords**:
   - Remove `admin_initial_password` field from `Tenant` model.
   - Remove password display and toggle scripts from `super_admin/dashboard.html`.
   - Implement secure administrative password reset mechanism.
   - Update tests to assert passwords are never stored or displayed in plaintext.
2. **Harden Tenant Security & Isolation**:
   - Disallow silent cross-subdomain fallback for tenant admins/staff.
   - Remove `is_staff` implicit tenant switching fallback in `resolve_requested_tenant`.
   - Implement comprehensive cross-tenant IDOR test suite covering all resources.
3. **Verify Firewall & Port Isolation**:
   - Confirm backing databases are unreachable externally.
4. **Secret Scan**:
   - Audit repository and configuration files for exposed secrets.
5. **Run Full Test Suite**:
   - Ensure all existing and new security tests pass cleanly.
