# SMS deprecation assessment

Date: 2026-08-17

## Scope

The SMS channel was traced across the communication configuration, message creation and sending flows, automations, backend APIs, persistence, payments, audit metadata, deployment configuration, dependencies, and documentation.

## Findings

SMS was implemented as a cross-layer feature rather than only a UI option. The active implementation included:

- a communication-configuration balance, SMS credit payments, and Stripe checkout/webhook handling;
- backend send, purchase, and payment-history endpoints;
- the `Message` SMS type and SMS serializers;
- UI configuration navigation, message forms, send-now flows, and automation editor/runtime branches;
- SMS choices in notification templates and reminders;
- audit registrations/labels, SMS provider settings, the `smsapi-client` dependency, and self-host configuration documentation.

## Changes completed

- Removed SMS navigation and configuration UI, including the deleted SMS settings section.
- Removed the SMS message type and option from message creation, sending, listing, and detail flows.
- Removed SMS actions and fields from automation creation and editing. Backend validation now permits only email message actions.
- Removed SMS API routes, serializers, provider calls, Stripe credit purchase side effects, audit references, settings, dependency, and self-host variables/documentation.
- Removed SMS choices from notification templates and reminders.
- Added forward migrations:
  - `communications.0011_remove_legacy_message_channels` removes the balance field and credit-payment table, narrows message choices, and disables/cleans workflows containing unsupported message actions.
  - `application.0428_remove_legacy_notification_channels` narrows legacy notification/reminder choices.
- Existing historical migrations were left unchanged to preserve Django’s migration graph. Their old schema identifiers, and the corresponding identifiers in the new removal migration, are intentionally retained so existing installations can migrate safely.

## Data and compatibility impact

- Applying the communications migration drops the SMS credit-payment table. Take the normal database backup before deployment if historical payment records must be retained for audit or recovery.
- Existing SMS `Message` rows are not deleted; list and detail APIs expose only email and in-app messages. A separate retention decision is needed before purging historical messages.
- Workflows containing an unsupported message action are sanitized and disabled during migration. Existing clients using removed SMS endpoints will receive a not-found response and must move to email or in-app communication.
- Any SMS provider credentials or Stripe price identifiers outside this repository should be revoked through the relevant infrastructure/provider consoles.

## Verification

Passed:

- Python compilation of changed backend modules.
- `git diff --check`.
- UI WebSocket test suite: 15 tests passed.
- Self-host static validation.
- Active product/configuration scan: no SMS references remain outside migrations and known third-party/generated content.

Not fully available in the local environment:

- Django migration dry-run could not start because Django is not installed in the host Python environment.
- The production UI build reached bundling but failed on the pre-existing `ArchiveBox` import in `UI/src/components/buttons/MoveToArchiveButton.svelte`; the installed `phosphor-svelte` package does not export that symbol. No SMS-related build error was reported.

## Recommended rollout checks

1. Run the two forward migrations in staging from a database backup.
2. Confirm affected automation workflows and historical messages with product/operations owners.
3. Verify Stripe webhook handling for supported payment events and confirm no SMS checkout sessions remain in use.
4. Revoke unused SMS provider credentials and price identifiers.
5. Deploy the UI and backend together because the API surface and persisted choices changed.
