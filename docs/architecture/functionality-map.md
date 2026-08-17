# Functional map: what the product currently does

This document maps major features to backend/API prefixes, frontend route families, and code surfaces.

## Core domain: Members and subscriptions

### What it includes

- Member/person registration and management
- Membership list views, details, import flows
- Membership lifecycle actions (add/edit/transfer/reject/archive/delete)
- Profile cards and personal access links
- Draft import/editing

### Evidence

- Backend prefixes: `subscription`, `personas`, `templates` (for sharing and module assignment context), `profile`, `search`
- Frontend families: `/members/*`, `/subscription*`, `/forms/*`, `/card/*`, `/invite/*`
- Route modules:
  - `UI/src/routes/association/Members/*`
  - `UI/src/routes/association/subscription/*`
  - `UI/src/routes/subscription/*`

## Core domain: Courses, attendance, and instructors

### What it includes

- Course catalog and detail view
- Course tags, locations, instructor management
- Enrolled members and attendance operations
- Overview per course/subscription
- Attendance scanners

### Evidence

- Backend prefixes: `course`, `course-locations`, `course-subscriptions`, `course-installment`, `instructor`, `attendance`, `attendance-day`
- Frontend families: `/course/*`, `/members/subscription` (indirect course membership views), `/attendance-scanner-mode`
- Route modules:
  - `UI/src/routes/association/course/*`
  - `UI/src/routes/association/carnet/*`
  - `UI/src/routes/association/instructor/*`

## Core domain: Camps and retreats

### What it includes

- Camp/retreat catalog and availability
- Periods and add-ons
- Subscriptions and subscriptions list management

### Evidence

- Backend prefixes: `camps-and-retreats`
- Frontend families: `/camps-and-retreats/*`, `/course/camps-and-retreats/*` (route-level integration in course UI)

## Core domain: Carnets and attendance tokens

### What it includes

- Carnet definition and card lifecycle
- Enrollment assignment, disable/enable/top-up/update
- Subscriptions for carnet usage

### Evidence

- Backend prefixes: `carnet`, `carnet-subscription`
- Frontend family: `/carnet/list`

## Core domain: Payments, accounting, and invoices

### What it includes

- Payment CRUD and simulation
- Payment categories and payment methods
- Invoice issuance + archive/reporting
- Customer/supplier ledgers
- Balance sheet / accounting accounts / transfers
- Import/export and simulation of recurring renewal payments

### Evidence

- Backend prefixes:
  - `payment`, `payment-bulk`, `invoice`, `invoice-customers`, `invoice-suppliers`
  - `bookkeeping` permissions map via frontend permission model and serializers
  - `balance-sheet`, `templates` (document-like ledger helpers)
- Frontend families: `/payment/*`, `/invoice/*`, `/accounting/*`, `/balance-sheet/*`, `/customers-invoice/*`, `/suppliers-invoice/*`
- Route modules:
  - `UI/src/routes/accounting/*`
  - `UI/src/routes/payment/*`
  - `UI/src/routes/invoice/*`

## Core domain: Communications and templates

### What it includes

- SMTP configuration
- Message composition and send flows
- Message history and workflows (automation)
- Template/module assignment and approvals

### Evidence

- Backend prefixes: `communications`, `modules`
- Frontend families: `/communication/*`
- Route modules:
  - `UI/src/routes/association/communication/*`
  - `UI/src/routes/association/course/camps-and-retreats` may compose module data through shared components

## Core domain: Reporting and auditability

### What it includes

- Saved reports and analytics reports
- Dashboards and KPI charts
- Audit trail viewing
- Export/import status and progress

### Evidence

- Backend prefixes: `report`, `audit-logs`, `saved-reports`, `association/export`, `association/import`, `export-all-data`
- Frontend families: `/report`, `/audit/list`, `/saved-reports`, `/accounting/*`

## Core domain: Onboarding and profile/admin

### What it includes

- Instance setup/configuration
- User role switching and profile sections
- Settings (integrations, two-factor, subscription/billing plan)
- Connected collaborators management

### Evidence

- Backend prefixes: `instance`, `onboarding`, `profile`, `two-fa`, `billing`, `google`, `onboarding`, `search`, `sport-associations`, `logo`, `manifest.json`, `status`
- Frontend families:
  - `/setup/*` and `/welcome`
  - `/profile`
  - `/tools/sport-associations-manager`
  - `/connected-collaborators`

## Core domain: Integrations and external connectors

### What it includes

- Google calendar sync and authentication callbacks
- Stripe onboarding, checkout, webhook hooks
- Email/SMTP verification
- AI/agent service and MCP export tooling
- PDF rendering pipeline for documents

### Evidence

- Backend prefixes: `google`, `stripe`, `communications`, `docmanager`, `application` AI/chat paths
- Frontend families: `/stripe/*`, `/communication/*`, `/camps-and-retreats/forms/*`
- Integration code:
  - `BE/application/views/stripe_views.py`
  - `BE/application/views/google_views.py`
  - `BE/communications/*`
  - `BE/application/mcp_server/*`

## Core domain: Document and artifact lifecycle

### What it includes

- Document archive, templates, retrieval, rendering endpoints
- Subscriptions/invoices exports by endpoint
- Document-based certificate workflows

### Evidence

- Backend prefixes: `document`, `documents`, `folders`, `printing`, `association/export`/`association/import`
- Frontend modules: `archive`, `templates`, `subscription` detail and forms
- Route modules:
  - `UI/src/routes/archive/*`
  - `UI/src/routes/templates/*`

## Domain boundary map: where logic actually lives

Use this as a practical “where to change code” matrix.

- **High-change areas**
  - `BE/application/*` and `UI/src/routes/association/*` (core workflows)
  - `BE/application/views/*`, `BE/application/models/*`, `BE/application/serializers/*`
- **Configuration or infra-level changes**
  - `BE/core/*`, `selfhost/*`
- **Functional add-ons**
  - `BE/communications/*`, `BE/docmanager/*`, `BE/application/mcp_server/*`

## What is not a first-class functional route

Some routes are infrastructure/API-only (health/check, schema, auth callbacks, etc.) and are not direct business features.
They are still part of architecture and should remain documented in `coverage-gaps.md`.
