# Phase 07.7 — Discovery & Current Implementation State (Before)

**Date**: 2026-09-28  
**Scope**: Seller Telegram registration, profile management, listing creation wizard, media handling, review, submission, dashboard review/approval/rejection, and seller notifications.

---

## 1. Existing New Project Architecture (`auctionBot`)

### A. Listing Domain (`apps/listings`)
- **`Listing` Model**:
  - Scoped to `Tenant` via `TenantOwnedModel`.
  - Fields: `seller_id` (CharField), `seller_username` (CharField), `title` (CharField), `description` (TextField), `category` (CharField), `listing_type` (`AUCTION`, `BUY_NOW`), `status` (`DRAFT`, `PENDING`, `APPROVED`, `REJECTED`, `CLOSED`), `quantity`, `remaining_quantity`, `metadata` (JSONField).
  - Validation: Clean checks `remaining_quantity <= quantity`.
- **Listing Services (`services/listings.py`)**:
  - `create_listing()`: Creates pending listing.
  - `approve_listing()`: Transitions `PENDING` -> `APPROVED`.
  - `reject_listing()`: Transitions `PENDING` -> `REJECTED`, records reason in `metadata['rejection_reason']`.
  - `close_listing()`: Transitions to `CLOSED`.
- **Gaps**:
  - No `Seller` model exists. Sellers are only loosely represented by string IDs (`seller_id`) on listings.
  - No `ListingImage` model exists for storing item photos/videos and linking them to listings.
  - No direct seller notification mechanism is wired when listing is approved or rejected in the dashboard.

### B. Telegram Infrastructure (`apps/telegram_engine`)
- **Models**:
  - `TelegramBotConfig`: Tenant-scoped, handles bot credentials (AES encrypted at rest), bot type (`UNIFIED`, `BUYER`, `SELLER`), webhook path `/telegram/webhook/<tenant_slug>/<bot_type>/`.
  - `TelegramUser`: Tenant-scoped user profile storing `telegram_user_id`, `chat_id`, `username`, `first_name`, `last_name`, `language_code`, `is_blocked`.
  - `TelegramUpdateLog`: Deduplication and audit log tracking `update_id` per tenant.
- **Dispatcher (`TelegramDispatcher`)**:
  - Routes `message` and `callback_query`.
  - Commands: `/start`, `/help`, and fallback for unknown commands.
  - Webhook handles secret token validation and duplicate update filtering.
- **Gaps**:
  - No persistent conversation state machine (`TelegramConversationState`).
  - No support for multi-step interactive workflows (questionnaires, wizards).
  - Photo/document message updates are not handled (only text messages and callback queries).
  - Telegram file download (`getFile`) is not implemented in `TelegramService`.

### C. Dashboard (`apps/dashboard`)
- **Listings Management**:
  - `listings_list_view`: Renders all, pending, approved, and rejected listings.
  - `listing_detail_view`: Shows listing details.
  - `listing_accept_action`: Approves listing via `listing_service.approve_listing()`.
  - `listing_reject_action`: Rejects listing via `listing_service.reject_listing()`.
  - `sellers_list_view`: Currently computes sellers dynamically by aggregating `Listing.objects.values('seller_id')`.
- **Gaps**:
  - Approving or rejecting a listing does not send a Telegram notification to the seller.
  - Image carousel/thumbnails are not displayed in the dashboard listing detail because `ListingImage` was not defined.

---

## 2. Legacy Project Analysis (`CYG_Aquatics_Malaysia` — Read-Only Reference)

- **Legacy Architecture**:
  - Monolithic script `fish_registration.py` (over 4,000 lines) with a massive `ConversationHandler`.
  - Used `Fish` model as a catch-all database record for both listing details, auction details, media files, and seller Telegram ID (`telegram_user_id`).
  - Accepted listings were duplicated into `AcceptedFishListings`.
  - Sellers did not have a dedicated profile table; seller identity was captured ad-hoc from Telegram message updates.
  - Media was saved into flat `photos/` and `videos/` directories without tenant isolation.
- **Legacy Workflow Strengths to Retain in Modern Architecture**:
  - Clean conversational question prompts (Title, Description, Category, Starting Price, Images).
  - Immediate review summary prior to final submission.
  - Explicit confirmation and admin notification.
- **Legacy Deficiencies to Avoid in Modern Architecture**:
  - No tenant isolation or data scoping.
  - Ephemeral in-memory conversation state vulnerable to process restarts.
  - No structured `Seller` entity with contact information.
  - Duplication between `Fish` and `AcceptedFishListings`.
  - Giant procedural `if/elif` ladders instead of modular state machine handlers.

---

## 3. Required Implementation Plan for Phase 07.7

1. **Seller Model (`apps/listings/models.py`)**:
   - `Seller(TenantOwnedModel)` with link to `TelegramUser`, business name, contact person, phone, email, address, and status (`PENDING`, `ACTIVE`, `SUSPENDED`).
2. **Listing Image Model (`apps/listings/models.py`)**:
   - `ListingImage(TenantOwnedModel)` with foreign key to `Listing`, image file path (tenant-isolated storage), `telegram_file_id`, and display order.
3. **Persistent Conversation State (`apps/telegram_engine/models.py`)**:
   - `TelegramConversationState(TenantOwnedModel)` tracking `(tenant, telegram_user, bot_type)` with state, current step, and recoverable JSON `context_data`.
4. **Seller Telegram Workflows (`apps/telegram_engine/seller_workflow.py`)**:
   - **Seller Registration Flow**:
     - Menu `/start` -> `[Register as Seller]`, `[Help]`.
     - Questionnaire steps: Business Name -> Contact Name -> Phone Number -> Email -> Address -> Confirm.
   - **Listing Creation Wizard**:
     - State `CREATING_LISTING`: Title -> Description -> Category -> Starting Price -> Buy-Now Price -> Quantity -> Photo Uploads.
     - Review draft summary screen with `[Submit Listing]`, `[Cancel]`.
     - Final submission transitions listing to `PENDING` for admin review.
     - Recovery mechanism on `/start`: detects incomplete draft and allows `[Continue Listing]` or `[Discard Draft]`.
5. **Media Download Service (`services/telegram.py`)**:
   - Implement `get_file()` and secure download into `media/tenants/<tenant_slug>/listings/`.
6. **Dashboard Integration & Seller Notifications**:
   - Update `listing_accept_action` and `listing_reject_action` to send a branded Telegram notification to the seller.
   - Update `listing_detail.html` to display listing images and seller contact information.
7. **Comprehensive Automated & IDOR Tests**:
   - Tenant isolation tests for sellers and listings.
   - State machine persistence and recovery tests.
   - Callback tampering and duplicate webhook idempotency tests.
