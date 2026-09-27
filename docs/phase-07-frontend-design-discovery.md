# Phase 07 — Frontend Design Discovery & Screen Inventory

## 1. Executive Summary & Design System Analysis

A comprehensive inspection of all 17 design artifacts in `E:\auctionbots\auctionBot\frontend screens` was conducted. The screens depict a unified, multi-tenant administrative dashboard called **AquaBid Dashboard** tailored for managing regional Telegram auction marketplaces.

### 1.1 Color Palette & Visual Theme
- **Sidebar**: Dark Slate Navy (`#0f172a` / `#0d172a`). Inactive navigation items use Slate 400 (`#94a3b8`); active items use Vibrant Royal Blue (`#2563eb` / `#1d4ed8`) with white text and rounded highlights (`rounded-lg`).
- **Background**: Soft neutral cool gray (`#f8fafc` / `#f1f5f9`).
- **Cards & Surfaces**: Clean white (`#ffffff`) with subtle 1px border (`#e2e8f0`), rounded corners (`12px` / `16px`, `rounded-xl`), and soft elevation shadows (`shadow-sm`).
- **Primary Brand Color**: Royal Blue (`#2563eb`), with hover state (`#1d4ed8`).
- **Status Pills / Badges**:
  - `Live`, `Active`, `Accepted`, `Connected`, `Highest`, `Resolved`: Soft Green background (`#dcfce7`), Forest Green text (`#166534`).
  - `Scheduled`, `Outbid`, `Pending`: Soft Amber (`#fef3c7`, text `#92400e`) or Soft Blue (`#dbeafe`, text `#1e40af`).
  - `Cancelled`, `Blocked`, `Rejected`, `Failed`, `Disabled`: Soft Red (`#fee2e2`), Crimson text (`#991b1b`).
- **Action Buttons**:
  - Primary: Filled Blue (`#2563eb`) with white text and icon.
  - Success: Filled Green (`#16a34a`) for "Accept Listing".
  - Danger: Filled Red (`#dc2626`) for "End Auction", "Reject Listing", "Stop Bot", "Reset Data".
  - Outline / Secondary: White background, Slate 300 border (`#cbd5e1`), Slate 700 text (`#334155`).

### 1.2 Layout Architecture
- **Sidebar (Left)**: Fixed 260px width. Features brand header (`AquaBid`), grouped navigation links with icons, collapsible accordions (`Listings`, `Auctions`, `Bids`, `Bot Management`, `Settings`), and bottom support/version card.
- **Top Bar (Header)**: Fixed height (64px), sticky. Contains sidebar toggle, Tenant Selector dropdown (`AquaBid Australia` / `CYG Malaysia`), live Bot status pill (`● Bot Connected` / `● Bot Online`), notification bell with red unread counter badge, and User Profile avatar + Name (`Shabi Husain`, `Admin`).
- **Main View Area**: Padded (`p-6` to `p-8`), max-width 100%, responsive grid (1, 2, 3, or 4 columns based on viewport).
- **Stat Cards**: Standardized 4-card or 6-card grid with colored icon container, bold counter, and descriptive label.
- **Filter Row**: Full-width card with text search, dropdown selects, date range, Search button, and Reset button.
- **Data Tables**: Clean row borders, avatar initials badges (`MK`, `JS`, `AL`, etc.), formatted currency amounts, status badges, and pagination footer (`Showing X of Y entries`).

---

## 2. Screen-by-Screen Inventory

### Screen 01: Bids Management (`0.jpeg`)
- **Name**: Bids Management (`All Bids`)
- **Route**: `/dashboard/bids/`
- **Purpose**: View and filter all bids placed on auctions across the tenant.
- **Reference Image**: `0.jpeg`
- **Major Components**:
  - Top 4 KPI Cards: Total Bids (`1,284`), Total Bid Value (`$18,420`), Highest Bid (`$220`), Unique Bidders (`342`).
  - Filter Bar: Search by user/auction/bid ID, Auction select, Bidder select, Status select, Date range, Search & Reset.
  - Table: `#`, `Auction` (linked), `Image`, `Bidder` (avatar + username), `Amount`, `Previous Bid`, `Bid Time`, `Status` (`Highest` in green, `Outbid` in blue), `Actions` (`View` button).
  - Pagination controls.
- **Responsive Notes**: On smaller viewports, cards stack 2x2 or 1x4, table scrolls horizontally.

---

### Screen 02: Auction Detail (`11.jpeg`)
- **Name**: Auction Detail View
- **Route**: `/dashboard/auctions/<id>/`
- **Purpose**: Comprehensive inspection and operational control for an individual auction.
- **Reference Image**: `11.jpeg`
- **Major Components**:
  - Header: Auction ID (`#507`), Title (`Red Dragon Fish`), `Live` badge, action buttons (`Edit`, `End Auction`, `Cancel Auction`).
  - Left Column: Multi-image photo gallery with thumbnail selector.
  - Middle Column: `Auction Information` card (ID, title, description, category, seller link, starting price, bid increment, start/end time, status, bid count, views).
  - Right Column:
    - `Current Highest Bid` card: Big price display (`$220`), highest bidder handle, timestamp, `View Live on Telegram` button.
    - `Time Remaining` countdown card (Hours, Minutes).
  - Bottom Left: `Bid History` table (`#`, `User`, `Amount`, `Time`).
  - Bottom Right: `Seller Information` card and `Admin Notes` card with Save button.
- **Responsive Notes**: 3-column top section stacks to single column on mobile/tablet.

---

### Screen 03: Users & Permissions (`2.jpeg`)
- **Name**: Users & Permissions Management
- **Route**: `/dashboard/users/`
- **Purpose**: Manage administrative and staff users, roles, and dashboard permissions.
- **Reference Image**: `2.jpeg`
- **Major Components**:
  - Top 4 KPI Cards: Total Users (`6`), Active Users (`5`), Admins (`2`), Staff (`3`).
  - Top Action: `+ Add User` button.
  - User List Table: `#`, `User` (avatar + name), `Email`, `Role` (`Owner`, `Admin`, `Staff` badges), `Last Login`, `Created`, `Status` (`Active`, `Disabled`), `Actions` (`View`, 3-dots menu).
  - Right Drawer / Modal: `Add New User` form (Name, Email, Password, Role select, Status select, Cancel/Create buttons, Role permission explainer).
  - Bottom Card: `Role Permissions Overview` matrix.
- **Responsive Notes**: Add User drawer slides over on mobile or sits side-by-side on large desktop.

---

### Screen 04: Auctions Management (`22.jpeg`)
- **Name**: Auctions List
- **Route**: `/dashboard/auctions/`
- **Purpose**: Manage live, scheduled, completed, and cancelled auctions.
- **Reference Image**: `22.jpeg`
- **Major Components**:
  - Header with `+ Create Auction` button.
  - Status Filter Tabs with counts: `All (24)`, `Live (8)`, `Scheduled (4)`, `Completed (10)`, `Cancelled (2)`.
  - Filter Bar: Search by title/seller/ID, Category, Seller, Status, Date range, Search & Reset.
  - Table: `#`, `Image`, `Title`, `Seller`, `Category`, `Starting Price`, `Current Bid`, `Bids`, `Start Time`, `End Time`, `Status`, `Actions` (`View`, menu).
- **Responsive Notes**: Horizontal scroll for table; filter fields wrap on tablet.

---

### Screen 05: Broadcast Messages (`3.jpeg`)
- **Name**: Broadcast Messages
- **Route**: `/dashboard/telegram/messages/`
- **Purpose**: Compose and broadcast announcements or alerts to Telegram users.
- **Reference Image**: `3.jpeg`
- **Major Components**:
  - Sub-navigation tabs: `Compose`, `Sent`.
  - Top Card: `Compose Message` (Bot selector, Recipient radio group [All Users, Sellers Only, Selected Users], Message text with character counter, Add Media dropzone, Schedule & Send Now buttons).
  - Bottom Card: `Sent Messages` history table (`#`, `Message`, `Bot`, `Recipients`, `Sent At`, `Delivered`, `Status`, `Actions`).
- **Responsive Notes**: Compose card stacks vertically on mobile.

---

### Screen 06: Bot Settings (`4.jpeg`)
- **Name**: Telegram Bot Configuration & Behavior
- **Route**: `/dashboard/telegram/settings/`
- **Purpose**: Configure bot credentials, webhook, behavior toggles, and automated messaging.
- **Reference Image**: `4.jpeg`
- **Major Components**:
  - Top 4 KPI Cards: Bot Status (`Online`), Bot Username (`@AquaBidAustraliaBot`), Total Users (`1,248`), Active Users (`892`).
  - Left Card: `General Settings` (Name, Username, Description, Language, Timezone).
  - Left Bottom Card: `Bot Behavior Settings` with toggle switches (Welcome Message, Auction Notifications, Bid Notifications, New Listing Alerts, Buy Now, Maintenance Mode, Anti-Spam, Require Registration, Inline Buttons, Admin Notifications).
  - Right Top Card: `Bot Status & Connection` (Status badge, Webhook Status, Masked Token with copy button, Webhook URL with copy button, Telegram link, Test Connection button, Restart button, Disconnect button).
  - Right Bottom Card: `Default Messages` (/start message, maintenance message) and `Advanced Settings` accordion.
- **Responsive Notes**: 2-column layout stacks on tablet/mobile.

---

### Screen 07: Listing Details (`44.jpeg`)
- **Name**: Listing Review & Moderation
- **Route**: `/dashboard/listings/<id>/`
- **Purpose**: Inspect seller submissions, review item specifications, and approve/reject listings.
- **Reference Image**: `44.jpeg`
- **Major Components**:
  - Header with `Back to All Enquiries` navigation.
  - Status Action Card: `Pending` badge, `Accept Listing` (green), `Reject Listing` (red), `Delete Listing` (outline).
  - Multi-image gallery with preview carousel.
  - `Basic Information` card: ID, Title, Description, Sales Type (`Auction` / `Buy It Now`), Category, Quantity, Starting Price, Buy-Now Price, Submission dates.
  - `Contact Details` card & `Images & Media` grid with download button.
  - `Seller Information` card.
  - `Admin Notes` form & `Activity Log` timeline.
- **Responsive Notes**: Image gallery adapts to single column on mobile.

---

### Screen 08: Telegram Bot Management Overview (`5.jpeg`)
- **Name**: Telegram Bot Management Overview
- **Route**: `/dashboard/telegram/`
- **Purpose**: Operational monitoring of Telegram bot health, uptime, and user activity.
- **Reference Image**: `5.jpeg`
- **Major Components**:
  - Top 6 KPI Cards: Bot Status (`Online`), Total Users (`1,248`), Active Users (`892`), Total Messages (`5,320`), Uptime (`99.9%`), Last Restart.
  - Navigation tabs: Overview, Commands, Broadcast Messages, Auto Messages, Bot Settings, Logs.
  - `Bot Information` card with quick restart/stop actions and masked credentials.
  - `Quick Actions` 4-card grid (Send Broadcast, View Users, Bot Settings, View Logs).
  - `Recent Bot Activity` table.
  - `Usage Statistics (Last 30 Days)` line chart with monthly totals.
- **Responsive Notes**: Quick action cards wrap from 4-across to 2x2.

---

### Screen 09: Listings & Enquiries (`555.jpeg`)
- **Name**: Listings Management (`All Enquiries`)
- **Route**: `/dashboard/listings/`
- **Purpose**: Seller submission queue and status filtering for listings.
- **Reference Image**: `555.jpeg`
- **Major Components**:
  - Header with `+ Add Listing` button.
  - Status Tabs: `All (24)`, `Pending (24)`, `Accepted (0)`, `Rejected (0)`, `Live (0)`, `Deleted (0)`.
  - Filter Bar: Search, Sales Type, Category, Seller, Date Range, Search & Reset.
  - Table: Checkbox, `#`, `Image`, `Title`, `Seller`, `Type` (`Auction` / `Buy It Now`), `Category`, `Price`, `Quantity`, `Contact`, `Submitted`, `Actions` (`Accept`, `Reject`, `View`).
- **Responsive Notes**: Bulk actions bar appears when checkboxes are checked.

---

### Screen 10: Wallet & Financial Accounts (`6.jpeg`)
- **Name**: Wallet & Balances
- **Route**: `/dashboard/wallets/`
- **Purpose**: Monitor user and seller wallet balances, credits, debits, and status.
- **Reference Image**: `6.jpeg`
- **Major Components**:
  - Top 4 KPI Cards: Total Wallet Balance (`$18,450`), Total Credits (In) (`$12,320`), Total Debits (Out) (`$6,130`), Wallet Users (`248`).
  - Sub-navigation tabs: `Wallet Users`, `Transactions`, `Payout Requests`.
  - Top Action: `Export` button.
  - Filter Bar: Search by name/username/ID, User Type (Buyer/Seller), Status (Active/Blocked/Pending), Balance Range, Search & Reset.
  - Table: `#`, `User` (avatar + name + handle), `Type` (`Seller` / `Buyer`), `Contact`, `Wallet Balance`, `Total Credits`, `Total Debits`, `Last Transaction`, `Status` (`Active`, `Blocked`, `Pending`), `Actions` (`View`, 3-dots).
- **Responsive Notes**: Table collapses secondary columns (`Total Credits`, `Total Debits`) on small screens.

---

### Screen 11: Main Dashboard Overview (`66.jpeg`)
- **Name**: Main Executive Dashboard
- **Route**: `/dashboard/`
- **Purpose**: High-level platform health, active auctions, listings summary, and recent activity.
- **Reference Image**: `66.jpeg`
- **Major Components**:
  - Date Range Picker in header.
  - Top 4 KPI Cards: Pending Listings (`24`), Live Auctions (`186`), Active Sellers (`86`), Telegram Users (`1,240`).
  - Middle Row Charts:
    - `Listings Overview` (Bar chart: Pending, Live, Completed).
    - `Sales Type` (Donut chart: Total Sales `238`, Auction `78%`, Buy It Now `22%`).
  - Bottom Row:
    - `Recent Listings` table with `View All` link.
    - `Recent Bids` table with `View All` link.
- **Responsive Notes**: 2-column charts stack vertically on tablet/mobile.

---

### Screen 12: Payments & Transactions (`7.jpeg`)
- **Name**: Payments & Ledger Transactions
- **Route**: `/dashboard/transactions/`
- **Purpose**: Auditable list of all financial transactions and payment records.
- **Reference Image**: `7.jpeg`
- **Major Components**:
  - Top 4 KPI Cards: Total Payments (`$12,840`), Completed (`$11,920`), Pending (`$620`), Failed (`$300`).
  - Filter Bar: Search by transaction ID/user/seller/auction, Payment Type dropdown, Status dropdown, Date range, Search & Reset.
  - Table: `#`, `Transaction ID`, `User / Seller` (avatar + handle), `Type` (`Auction Payment`, `Buy Now`, `Wallet Top Up`, `Payout`), `Related To` (entity link), `Amount`, `Payment Method` with logo/icon, `Date`, `Status` (`Completed`, `Pending`, `Failed`, `Refunded`), `Actions` (`View`, 3-dots).
- **Responsive Notes**: Payment method icon and status badge stay prominent on mobile.

---

### Screen 13: Extended Analytics Dashboard (`77.jpeg`)
- **Name**: Extended Platform Dashboard
- **Route**: `/dashboard/` (Extended / High-density view)
- **Purpose**: In-depth analytics including revenue breakdown, categories, top sellers, and upcoming auctions.
- **Reference Image**: `77.jpeg`
- **Major Components**:
  - Top 6 KPI Cards with percentage trends (+12%, +8%, +22%, etc.): Pending Listings, Live Auctions, Total Bids, Buy It Now, Active Sellers, Telegram Users.
  - Middle Charts: `Revenue Overview` (Auction vs Buy Now bars), `Listings Status` (multi-color donut), `Sales Type` (donut).
  - Activity Row: `Recent Listings`, `Recent Bids`, `Bot Activity` live feed.
  - Bottom Row: `Top Categories` breakdown, `Top Sellers` leaderboard (1, 2, 3 medals), `Upcoming Auctions` list.
- **Responsive Notes**: Bottom 3-column section stacks cleanly into single columns.

---

### Screen 14: Telegram Users (`8.jpeg`)
- **Name**: Telegram Users Directory
- **Route**: `/dashboard/telegram/users/`
- **Purpose**: Directory of Telegram accounts registered through the bot.
- **Reference Image**: `8.jpeg`
- **Major Components**:
  - Top 4 KPI Cards: Total Users (`1,248`), Active Users (`892`), New Users (Last 30 Days) (`312`), Blocked Users (`44`).
  - Filter Bar: Search by name/username/Telegram ID, Status, Joined Date, User Type, Date range, Search & Reset.
  - Table: `#`, `User` (avatar), `Telegram ID`, `Username`, `Name`, `Joined Date`, `Total Bids`, `Total Purchases`, `Status` (`Active`, `Blocked`), `Actions` (`View`, 3-dots).
- **Responsive Notes**: Telegram ID and joined date collapse on mobile; name/username remain primary.

---

### Screen 15: Sellers Management (`9.jpeg`)
- **Name**: Sellers Directory
- **Route**: `/dashboard/sellers/`
- **Purpose**: Manage approved, pending, and blocked sellers.
- **Reference Image**: `9.jpeg`
- **Major Components**:
  - Top action: `+ Add Seller` button.
  - Top 4 KPI Cards: Total Sellers (`48`), Active Sellers (`42`), Pending Approval (`4`), Blocked Sellers (`2`).
  - Table: Checkbox, `#`, `Avatar`, `Name / Username`, `Contact`, `Total Listings`, `Active Auctions`, `Total Sales`, `Joined Date`, `Status` (`Active`, `Pending`, `Blocked`), `Actions` (`View`, 3-dots).
- **Responsive Notes**: Checkbox and stats adapt for batch management.

---

### Screen 16: Business Settings (`WhatsApp Image 2026-09-24 at 04.12.10.jpeg`)
- **Name**: Business & Tenant Settings
- **Route**: `/dashboard/settings/`
- **Purpose**: Manage tenant business profile, branding, currency, timezone, and operational flags.
- **Reference Image**: `WhatsApp Image 2026-09-24 at 04.12.10.jpeg`
- **Major Components**:
  - Sub-navigation tabs: `General`, `Branding`, `Domain`, `Email & Notifications`, `Integrations`, `Billing`, `Advanced`.
  - Left Form: `Business Information` (Business Name, Business Type, Email, Timezone, Phone, Currency, Website, Date Format, Address), `Business Description`, `Contact Information`, Save button.
  - Right Cards: `Business Logo` uploader, `Favicon` uploader, `Platform Settings` toggles (Allow New Seller Registrations, Require Email Verification, Enable Public Marketplace, Maintenance Mode), `Danger Zone` (Reset Tenant Data button in red).
- **Responsive Notes**: Two-column layout stacks on tablet/mobile.

---

### Screen 17: Helpdesk & Support Tickets (`WhatsApp Image 2026-09-24 at 1.jpeg`)
- **Name**: Helpdesk & Support Tickets
- **Route**: `/dashboard/helpdesk/`
- **Purpose**: In-app customer support ticketing and user inquiries.
- **Reference Image**: `WhatsApp Image 2026-09-24 at 1.jpeg`
- **Major Components**:
  - Top 4 KPI Cards: Open Tickets (`12`), Pending Tickets (`8`), Resolved Tickets (`25`), Total Tickets (`45`).
  - Left Panel: Ticket List Table (`#`, `Ticket ID`, `User`, `Subject`, `Bot`, `Created`, `Last Updated`, `Status` [`Open`, `Resolved`, `Pending`], `Actions`).
  - Right Panel: Active Ticket Conversation (`#TKT-1045` `Open`), user profile details, chat message bubbles with timestamps, attachment previews, reply textarea, `Send Reply` button.
- **Responsive Notes**: On desktop, master-detail side-by-side view; on mobile, ticket list transitions to detail view on click.

---

## 3. Technology & Architecture Decision

To deliver maximum fidelity to these exact designs with zero latency, native Django authentication/session integration, bulletproof tenant isolation, and zero build tool complexity:

1. **Architecture**: Server-Rendered Django Views + Reusable Components with Tailwind CSS (via official Tailwind CDN/utility bundle) and Chart.js for pixel-matching graphs.
2. **Advantages**:
   - Zero compilation or node_modules dependencies needed to run or test.
   - Built-in Django session authentication, CSRF tokens, and permission decorators (`@login_required`, `@user_passes_test`).
   - Seamless integration with existing Phase 01–06 domain services (`services.auctions`, `services.bids`, `services.listings`, `services.finance`, `services.telegram`).
   - Server-side filtering, sorting, and pagination (`Paginator`) natively protecting against N+1 queries.
   - Exact CSS utility replication of colors, rounded radii (`rounded-xl`), typography, and badges shown in the 17 design screens.
3. **Application Location**: `apps/dashboard/` with templates in `apps/dashboard/templates/dashboard/` and static assets in `static/dashboard/`.
