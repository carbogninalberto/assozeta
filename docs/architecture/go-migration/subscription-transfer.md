# Subscription transfer (subscription/<uid>/transfer) — implementing with HTTP parity

`POST subscription/<uid>/transfer` (`subscription_transfer`, `BE/application/views/subscriptions_views.py`) is implemented in Go
(`BE_GO/internal/{domain,repository,service,handler}/subscription_transfer.go`). Registry `association.members.update`. Django
remains the execution owner.

## Behaviour

`@api_view(['POST'])` with `IsAuthenticated`: `Allow: POST, OPTIONS`; OPTIONS names `Subscription Transfer` with an empty
description. Restricted collaborators get the registry 403, an administrator without a session the impersonation 403, an owner's
association-scoped session on an athlete `Operazione non disponibile nel contesto dell’associazione.`. The view is not atomic:
every statement and `save()` commits on its own. In Django's order:

1. `is_valid_uuid(uid)`: 400 `["badly formed hexadecimal UUID string"]`.
2. `scoped_queryset(request, Subscription.objects).get(subscription_id=uid)`: the group-aware `SubscriptionManager` (a valid
   `X-Group-Id` filters by group), the impersonation association when the session has one; 404 `{"msg": "Subscription not found."}`.
3. `subscription.user != request.user`: 403 `{"msg": "You are not the owner of the subscription."}`. Collaborators act as the
   owner; an athlete can transfer a subscription whose user is the athlete.
4. `SubscriptionTransfer.objects.filter(expires_at__lt=now()).update(status=EXPIRED)`: every transfer whose expiry has passed
   becomes status 4 (a queryset update: no audit entry). This write happens for every request that reaches this point.
5. A PENDING transfer of the subscription: 400 `{"msg": "C'è già una richiesta di trasferimento in attesa di essere approvata."}`.
6. `request.data.get(...)`: a body that is not a dict is a 500; a body parse error is raised here (after the expiry update).
7. With `recipient` (not None; form bodies use the last value):
   - equal to `str(request.user.user_id)`: 409 `{"msg": "You cannot transfer the subscription to yourself."}`;
   - `User.objects.filter(user_id=recipient).exclude(user_id=request.user.user_id).first()`: the UUIDField converts a string as
     `UUID(hex=...)` and an int (bool included) as `UUID(int=...)`; anything else (floats, lists, malformed text) is a 500; no
     user is a 400 without a body (an upper-case spelling of the requester's own id lands here).
8. Without `recipient`: `email` None is 400 `{"msg": "Email is required."}`. Otherwise `get_main_tutor()` (the first primary
   relation by id, else the first relation, which is made primary with its update entry) gives the names (the associate's when
   there is no tutor); `username = (first[:3] + last[:3] + 8 random letters/digits).upper()`, an 8-character random password,
   `User.objects.create_user(..., role=ATHLETE)` (normalize_email: `email or ''`, the domain lower-cased; a non-string truthy
   value is a 500 before the row) with its create entry (indexed through the actor's association), then
   `AuthUtils.send_password_welcome_email(user, password, request.user.sport_association)`: an athlete is DRF's 403
   `User is not a sport association` (the account stays); otherwise `render_to_string('email/account/email_welcome_password_message.html')`
   under the request language and `send_mail_async.apply_async(kwargs={subject: '<WHITELABEL_NAME> | Benvenuto', message, from_email,
   recipient_list: [email], html_message, fail_silently: False})` published from Go (`internal/celery`, `internal/emailrender`).
9. `NotificationService.send_notification(recipient, [{'type': 'subscription', 'msg': "l'associazione <denomination> ha trasferito
   l'iscrizione <associate full name> al tuo account"}], association_id=subscription.sport_association_id)`: the broadcast rows
   (created once per user, audited), the Redis DB 0 list, the `user_broadcasts` cache and the Channels `notification_push` to
   `notifications_user_<recipient>` (written by Go with `internal/channels`, plus the mirrored envelope). Push failures are
   logged, storing failures are a 500 (`SubscriptionNotifier.Deliver`).
10. `SubscriptionTransferCreateSerializer.save()` (status 1, `expires_at` now + 30 days, uuid4 token; create entry indexed
    `subscription.sport_association`), `subscription.user = recipient; save()` (update entry, direct), the associate's user and
    `save()` (entry only when it changes), `get_main_tutor()` again and the tutor's user, then `status = ACCEPTED`,
    `expires_at = now()` and `save()` (update entry with both fields).
11. 201 with the serializer's data: `{subscription, expires_at, requester, recipient, token, status: 2}`.

## Not ported

Refused with the 500 page before the first write (the expiry update), decided read-only with the same lookups:
- a subscription with an empty `start_date` or `end_date` when the request would reach `Subscription.save()` (it fills them from
  the owner's season settings);
- the e-mail branch when the first three characters of the tutor's/associate's first or last name are not ASCII (Python's
  `str.upper()` special casing and the username's NFKC normalization), or when the e-mail's domain is not ASCII (`str.lower()`).

## Evidence

`TESTS/fixtures/subscription_transfer_http.json` (29 cases, `TESTS/fixtures/generate_subscription_transfer_http.py`), contract
`TESTS/contracts/test_subscription_transfer_http.py`, adapter `TESTS/adapters/django/subscription_transfer_http.py`. Every case
checks the complete model state (transfers with the expiry rule, the new transfer bound to the response's token and expiry,
subscription/associate/tutor users, relations, new accounts with their defaults and a password of the default hasher that matches
the one in the welcome e-mail), the audit entries and index rows in order, the Celery welcome e-mail against Django's own
`render_to_string` for the stored account, and the recipient's notification store, cache, Channels push and mirrored envelope.
The welcome template is also pinned byte for byte by `go test ./internal/emailrender` (three renders added to
`TESTS/adapters/go/email_render_golden.py`).

- RED (`red-r2`, route unregistered): Django 29/29; Go 404 on the first case.
- GREEN (`versioned-r1`): 29/29 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`mutation-r1`): expiring only PENDING transfers (`AND status=1` in the expiry update) fails `subscription-transfer-pending-transfer` (CAUGHT).
- `subscription-create` (38/38), `websockets` (75/75), `email-render-routes` (82/82) and `oauth-accounts` (109/109) re-run green after the notifier split and the new template (`Send` = store + push; `Deliver` swallows push errors).

Model hooks ([model-hooks](model-hooks.md)): `subscription-transfer-invalid-season-settings-after-transfer-server-error`:
the owner of a seeded association whose season start is 31 April (a minted access token) transfers its dated subscription.
Django writes the notification and the PENDING transfer, then `subscription.save()` raises in the season range: 500, users
unchanged. Go now checks the same range at that point. Native 30/30; mutation (`model-hooks-mutation`) fails Go with 201
(CAUGHT).
