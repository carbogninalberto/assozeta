# Subscription reject, archive and delete — implementing with HTTP parity

`POST subscription/<uid>/reject`, `POST subscription/<uid>/archive` and `POST subscription/<uid>/delete`
(`subscription_reject`, `subscription_archive` and `subscription_delete` in `BE/application/views/subscriptions_views.py`, the last
through `SubscriptionService.delete_subscription`) are implemented in Go (`BE_GO/internal/{domain,repository,service,handler}/subscription_lifecycle.go`).
Registry `association.members.update`, `association.members.archive.update` and `association.members.delete`. Django remains the
execution owner. `subscription/<uid>/approve` shares the prologue and is documented in [subscription-approve](subscription-approve.md).

## Shared behaviour

- `@api_view(['POST'])`: `Allow: POST, OPTIONS`; OPTIONS names `Subscription Reject`, `Subscription Archive` and `Subscription Delete`
  (no docstrings). Archive also has `IsAssociationUser`: an athlete gets DRF's generic 403, also for OPTIONS and other methods.
- `scoped_queryset(SportAssociation.objects).get(user=request.user)`: athletes and owners without an association are a 500 (the HTML
  page, also inside the archive/delete `try`, because `Response({'exception': e})` cannot render the exception).
- `scoped_queryset(Subscription.objects)`: the group-aware `SubscriptionManager` (`X-Group-Id`), which does not hide soft-deleted rows;
  under an association impersonation it is limited to that association. Another association's row is `PermissionDenied('User not allowed.')`.
- `Subscription.save()` fills missing start/end dates from the association owner's settings (`ResolveSubscriptionDates`); only changed
  fields are audited, indexed `direct`. Subscription entries render `Subscription object (<pk>)`.

## Reject

`is_valid_uuid` first (400 list). A miss is `404 {'msg': 'Subscription not found.'}`, another association's row a DRF 403
`User not allowed.`, an already rejected row a DRF 403 `User cannot change the status of the subscription.`. Otherwise `status_flag=3`,
`acceptance_date=None` and the save; `{'data': {'msg': 'subscription rejected.'}}`.

## Archive

Inside the `try`: `PermissionDenied` renders `403 {'exception': 'User not allowed.'}`; a uid the UUID field rejects raises a Django
`ValidationError`, which DRF's encoder iterates: `500 {'exception': ['“<uid>” is not a valid UUID.']}`; a miss is
`404 {'exception': 'Subscription not found'}`. The flag is toggled. Unarchiving only saves the subscription. Archiving, in order:

1. for each live course subscription (`CourseSubscription.objects`, scoped): its payment (forward relation, base manager) when unpaid is
   saved archived (`Payment.save()` fills a null category / description, as in `payment-archive.md`); for a multi-payment row each live
   group-aware installment's unpaid payment is saved archived and the installment saved archived (indexed
   `course_subscription.course.sport_association`); the course subscription is saved archived (its `str(self.__dict__)` repr, indexed
   `course.sport_association`);
2. the subscription's unpaid payment is saved archived, then the subscription;
3. every live group-aware unpaid unarchived payment of the association and the subscription's associate created between the start and end dates (midnight UTC, both inclusive) is saved archived.

`{'data': {'msg': 'subscription archival completed.'}}`. Saves that change nothing write no entry.

## Delete

Inside the same kind of `try` (no `is_valid_uuid`: a malformed uid is the 500 list above; a miss is `404 {'msg': 'Subscription not found.'}`):

1. for each live course subscription (not scoped, not group-aware): an unpaid payment is deleted (Payment's `pre_delete` first deletes the
   payment's live group-aware installments, whose own `pre_delete` deletes the payment: entries P, I, P); for a multi-payment row each
   live group-aware installment with an unpaid payment has the payment deleted and is then deleted again with its payment cleared in memory
   (a fourth entry for a row already gone); paid ones are saved archived; then the row's soft delete (its live group-aware installments
   through the collector, then `deleted=True`, entry before the update);
2. the subscription's unpaid payment is deleted, the subscription saved archived;
3. the live group-aware unpaid unarchived payments of the association and associate between the dates are deleted (one collector);
4. `subscription.delete()`: the collector loads the subscription, all its course subscriptions (soft-deleted included) and their
   installments; `pre_delete` runs for the installments (unpaid payments deleted as above), then for the course subscriptions
   (`delete_related_course_payment`: nothing for a row with a payment, otherwise a membership's unpaid payments and the live installments);
   tag, membership-payment and carnet links go; the subscription, installments and course subscriptions are deleted with entries in that
   model order, instances in reversed key order. Payment and subscription deletes are indexed `changes_json.sport_association`,
   installment and course subscription deletes through the actor's own association (none for a collaborator).

`{'data': {'msg': 'subscription delete.'}}`. SET_NULL references to deleted payments are cleared.

## Go implementation

Each request is one transaction. Django's unordered querysets (course subscriptions, installments, period payments) are read in the
table's physical order, which is the order Django iterates them; collector sets are key ordered. The payment save reuses
`paymentSaveDefaults` and the payment archive statements; the delete cascade reuses the course subscription collector
(`deletePayments`, `deleteInstallments`, `DeleteInstallmentsOf`, `SaveCourseSubscription`, `CourseSubscriptionRepr`).

## Ported for API v2 (v2-subscriptions)

- Invoices of unpaid payments: `payment.invoice.delete()` is Django's Collector from the invoice (its rows first, the many-to-many
  rows, `Payment.invoice` set NULL), with the generic graph collector (`collaborator_delete_graph.json`).
  - Archive, course-subscription and subscription payments: the invoice is deleted, then `payment.save()` raises ValueError (the cached
    invoice has no key). The answer is the HTML 500 page; the invoice delete and earlier saves stay (Go commits them first).
  - Archive, installment payments: the payment is saved archived, then its invoice deleted; the request goes on.
  - Delete: the invoice is deleted, then the payment. auditlog renders the payment's in-memory `invoice` (the deleted invoice's id);
    the nested delete through the installment's `pre_delete` renders the stored row (`None`).
- Archive of a soft-deleted unpaid payment (the forward accessor uses the base manager): the save is audited as an update whose
  changes are the diff against None (auditlog's `Payment.objects` lookup misses the row), indexed through
  `changes_json.sport_association`.
- Delete: `subscription.delete()` is the generic Collector from the subscription, so every row that references it is deleted with it
  and audited in Django's model order (medical appointments, memberships, sold products, tokens, files, transfers, camp subscription
  periods and carnet subscriptions first, then the subscription, then the camp subscriptions), with the many-to-many rows.
- Delete of a signed subscription: after the commit, `delete_signature_key_if_unreferenced` deletes the storage object when no row
  (base manager) keeps the key; failures change nothing else.

## Not ported (refused with a 500 before any write)

- Archive: an unpaid payment hidden by `X-Group-Id` (the UI never sends it); a payment without an association; a `Payment.save()` that
  raises (account transfers without a description); a subscription whose dates stay empty after the save defaults. When one of these
  comes after an invoiced payment in Django's order, Go refuses the whole request where Django would first delete that invoice.
- Delete: an unpaid payment reached twice (the second access fails in Django after earlier deletes); a subscription whose dates stay
  empty.
- Django is not atomic: work saved before a failure stays. The Go port commits everything or nothing, except the archive's invoice
  ValueError above.

## Evidence

`TESTS/fixtures/subscription_lifecycle_http.json` (63 cases, generated by `TESTS/fixtures/generate_subscription_lifecycle_http.py`).

- RED (`native-r1`): Django 51/51; Go 404 on the first case.
- GREEN (`versioned-r1`): 51/51 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact subscription, payment, course
  subscription, installment, link, audit and index effects.
- Mutation (`mutation-r1`): dropping the `X-Group-Id` filter from the installment query fails `subscription-lifecycle-archive-group-scoped` (CAUGHT).
- `subscription-tags` and `subscription-memberships` stay green after the route block was added to `main.go`.

`delete-membership-and-deleted-rows`: subscription.delete() runs `delete_related_course_payment` for a payment-less membership
row (soft-deleted by the service just before: its live unpaid membership payments are deleted, the paid one stays) and for a
soft-deleted row (its remaining live installment is deleted, then deleted again by the collector).

- Django (`gaps-native-1`): 52/52 before any Go change (native Go also 52/52).
- GREEN (`gaps-versioned-1`): 52/52 on native Django, native Go, Caddy `/api/v1` and `/api/v2`.
- Mutation (`gaps-mutation-1`): deleting paid membership payments too fails `delete-membership-and-deleted-rows` (CAUGHT).

v2-subscriptions (11 cases: `delete-related-rows`, `delete-signed-*`, `archive-soft-deleted-payment`, `archive-invoiced-*`,
`delete-invoiced-*`). The runner owns a private MinIO (`service_context`) for the signature objects, and every workload starts with
the payment, course-subscription and installment tables clustered by key (the views read them in heap order).

- RED (`v2sub-red-r5`): Django 63/63; Go 500 on `subscription-lifecycle-archive-soft-deleted-payment`.
- GREEN (`v2sub-versioned-r3`): 63/63 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with the related rows, invoices,
  storage objects, audit and index effects.
- Mutation (`v2sub-mutation-r2`): rendering the stored invoice instead of the in-memory one fails
  `subscription-lifecycle-delete-invoiced-subscription-payment` (CAUGHT).
- `subscription-approve` (27/27) and `celery-routes-a` (76/76) share the repository and re-ran green (`v2sub-rerun-r1`).
