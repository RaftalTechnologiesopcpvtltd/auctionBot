# Phase 07.5 — Tenant Security Hardening

## Overview
This document specifies the hardened multi-tenant security architecture implemented in Phase 07.5. It governs tenant isolation, role definitions, tenant context switching rules, IDOR prevention, and financial authorization.

---

## 1. Role Definitions & Hierarchy

The platform defines three authorization tiers:

### A. Platform Administrator (`PLATFORM_ADMIN`)
* **Qualification**: User with `is_superuser = True` or explicit `TenantMembership` with role `PLATFORM_ADMIN`.
* **Privileges**:
  * Cross-tenant visibility across all registered organizations.
  * Can arbitrarily switch active tenant context (`switch_tenant_view`).
  * Can view platform-wide telemetry, create new organizations, and manage platform staff.
  * Can issue financial refunds and reversals across any tenant.

### B. Tenant Administrator (`TENANT_ADMIN`)
* **Qualification**: User with `TenantMembership` where `role = TENANT_ADMIN` for a specific `Tenant`.
* **Privileges**:
  * Full administrative rights strictly locked to their assigned organization.
  * Cannot switch tenant context under any circumstances (switching attempts return `403 Forbidden`).
  * Authorized to execute financial actions (refunds, reversals) strictly within their own tenant ledger.
  * Authorized to update organization business settings (`/dashboard/settings/business/`).
  * Authorized to invite and manage staff members within their own organization.

### C. Tenant Staff (`TENANT_STAFF`)
* **Qualification**: User with `TenantMembership` where `role = TENANT_STAFF` for a specific `Tenant`.
* **Privileges**:
  * Operational access to listings, active auctions, and general inquiries within their tenant.
  * Cannot switch tenant context (attempts return `403 Forbidden`).
  * Strictly forbidden from executing financial refunds, reversals, or adjustments (`403 Forbidden`).
  * Strictly forbidden from modifying business profile settings (`403 Forbidden`).

---

## 2. Centralized Tenant Authorization Engine (`apps.tenants.security`)

All authorization decisions flow through the centralized engine in `apps/tenants/security.py`:

```
resolve_requested_tenant(user, requested_tenant_id)
                     ↓
             is_platform_admin?
               /            \
             YES             NO
              ↓               ↓
         allow switch    fetch assigned tenants
                              ↓
                      is requested in assigned?
                         /           \
                       YES            NO
                        ↓              ↓
                     allow        raise PermissionDenied
```

### Core Functions:
1. `is_platform_admin(user)`: Returns True if superuser or active platform admin membership.
2. `is_tenant_admin(user, tenant)`: Returns True if user holds admin privileges for the tenant.
3. `is_tenant_staff(user, tenant)`: Returns True if user holds staff or admin privileges for the tenant.
4. `can_switch_tenant(user)`: Strictly returns `is_platform_admin(user)`.
5. `resolve_requested_tenant(user, requested_id)`: Rejects unauthorized tenant switching.
6. `enforce_tenant_object_access(obj, tenant)`: Raises `Http404` on cross-tenant mismatch to protect against IDOR without leaking object existence.

---

## 3. Session Hardening & Anti-Tampering

* **Session Validation**: `@dashboard_auth_required` decorator dynamically resolves `request.tenant` using `resolve_requested_tenant`.
* **Forged Session Defense**: If an attacker modifies the session cookie (`active_tenant_id`) to point to a foreign tenant ID, the authorization engine overrides the forged ID and clamps context back to the user's authorized tenant.
* **Logout Cleanup**: `logout_view` flushes the session, clearing any stored tenant state.

---

## 4. UI Hardening

* **Tenant Switcher Visibility**: In `apps/dashboard/templates/dashboard/base.html`, the switcher dropdown is rendered strictly inside `{% if can_switch_tenant %}`.
* **Tenant User Badge**: Tenant Admins and Staff are presented with a static, non-interactive organization badge displaying their organization name and code.

---

## 5. Cache & Celery Isolation

* **Tenant-Scoped Cache Keys**: `apps/core/cache.py` provides `get_tenant_cache_key(tenant, key_name)` yielding `tenant:{tenant_id}:{key_name}`. This prevents Tenant A from ever retrieving cached calculations or metrics belonging to Tenant B.
* **Celery Task Verification**: Background tasks (e.g., `close_auction_task`) explicitly require `tenant_id` and query with `.filter(tenant=tenant)`. Mismatches abort cleanly without retrying.

---

## 6. Financial Authorization

* Standard staff permissions do not permit financial mutations.
* `/dashboard/transactions/<id>/refund/` and `/dashboard/transactions/<id>/reverse/` strictly enforce `is_platform_admin(user) or is_tenant_admin(user, tenant)`.
* Requests by tenant staff return `403 Forbidden`.
