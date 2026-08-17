# Goal prompt: completely deprecate SMS

Implement the complete removal of the SMS communication channel from Assozeta.

## Objective

Users must no longer be able to configure, compose, send, purchase, select, or automate SMS messages. Supported communication channels after the change are email and in-app messages, with push notifications where already supported by the existing reminder model.

## Required work

1. Inspect the repository first and map every active SMS reference across UI, backend, routes, models, serializers, automation runtime/editor, audit code, payments, settings, dependencies, tests, and documentation.
2. Remove the SMS entry from `/communication/configuration` and remove SMS from the message-type field and all message forms under `/communication/messages`.
3. Remove SMS actions, options, validation, and rendering from automations. Existing automation data containing unsupported message actions must be handled safely and must not continue sending SMS.
4. Remove backend SMS endpoints, provider integrations, serializers, model fields/models, message enum choices, audit references, Stripe credit purchase/webhook logic, environment variables, package dependencies, and documentation.
5. Add forward migrations for persisted schema/choice changes. Do not rewrite historical migrations; retain only the old identifiers required by the forward migration to remove legacy schema safely.
6. Preserve unrelated working-tree changes. Run focused tests, static checks, compilation, migration checks, and the relevant UI build; distinguish pre-existing failures from regressions.
7. Prepare an assessment report covering scope, data impact, compatibility, residual historical references, verification, and rollout risks.

## Acceptance criteria

- No active product or configuration code contains an SMS feature path, option, provider call, or endpoint.
- `/communication/configuration` has no SMS section or balance/purchase UI.
- `/communication/messages` offers no SMS message type and cannot submit an SMS request.
- Automation UI and backend validation support email message actions only; unsupported persisted workflows are safely disabled or migrated.
- Backend schema and migrations remove the SMS balance/payment model and obsolete notification choices.
- SMS provider settings, dependency entries, audit references, and Stripe credit logic are removed.
- Historical migrations remain intact, and their necessary legacy identifiers are documented.
- Verification results and any environment limitations are reported.
- Changes are staged selectively and committed using the repository’s conventional commit format.
