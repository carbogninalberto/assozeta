# Subscription memberships list, add, update and delete — implementing with HTTP parity

`GET subscription/memberships/list`, `POST subscription/memberships/add`, `PATCH subscription/memberships/<pk>/update` and `DELETE subscription/memberships/<pk>/delete` (`SubscriptionMembershipViewSet` list/add/update/delete) are implemented in Go (`BE_GO/internal/{domain,dto,repository,service,handler}/subscription_membership(s)*.go`). Registry `association.members.read`, `.create`, `.update` and `.delete`.

- `get_queryset()` runs `SportAssociation.objects.get(user=request.user)` (a miss is a 500, also for athletes), then filters by the optional `subscription` and `associate_id` query parameters. A blank value is ignored. A value the UUID column rejects is a 500. `list` renders `{'data': [...]}`, unordered. `paid` is `payment.paid` through the forward relation's base manager, so a soft-deleted payment still renders. `attached_membership_documents` is nested.
- `update` and `delete` start with `get_object()` on that queryset, so the query parameters also narrow the pk lookup. A malformed pk is a bare `Not found.`; an unknown, foreign or filtered-out one is `No SubscriptionMembership matches the given query.`.
- `add` first assigns `request.data['sport_association']` from the `User.sport_association` property:
  - athletes get `PermissionDenied('User is not a sport association')` (403);
  - an owner without an association gets a 500;
  - a list or scalar body, or a form body (an immutable `QueryDict`), is a 500;
  - a client `sport_association` never wins.
- `SubscriptionMembershipSerializer` validates these fields:
  - a writable `subscription_membership_id` with its UniqueValidator;
  - tenant-scoped relations: `sport_association` (all associations), `associate` (soft-delete, group-aware) and `payment` (live, group-aware). A relation to another tenant's row is DRF's generic 403, which escapes before the field errors. A missing row, including one hidden by `X-Group-Id`, is a 400. An empty value is None.
  - `creation_date` (DateTimeField, ISO input, rendered UTC);
  - `start_date` and `end_date`. `REST_FRAMEWORK['DATE_INPUT_FORMATS']` is `['%d/%m/%Y']`, ported as CPython's `_strptime` regex with Unicode digits; dates render ISO.
  - `membership_type` (255, nullable, not blank), `membership_number` (50, nullable, blank, default `0`) and `description` (255, nullable, blank);
  - `price` (DecimalField(9, 2); a blank value is None);
  - a write-only `documents_data` list of dicts.
  `subscription` is not a serializer field and is ignored. OPTIONS on `add` describes the serializer under `actions.POST`.
- `update` is partial. It saves every field and audits the changed ones (200); an unchanged update writes no entry. `delete` removes the document links (the M2M table is `application_subscriptionmembership_attached_membership_docu749f`, Django's 63-character truncation) and the membership (204). The documents stay. Audit entries are indexed to the membership's association (`direct` for create/update, `changes_json.sport_association` for delete).
- The viewset's own `delete()` answers DELETE on every mapped route: on `list` and `add` (no pk) it is a 500, and on `update` it deletes the membership. `Allow` lists the mapped method (plus HEAD for GET), DELETE and OPTIONS. A DELETE never reads its body.
- Requests are not atomic. The serializer saves before handling `documents_data`, so a document without `document` and `filename` (400 `{'exception': 'document key not present'}`) or with undecodable base64 (500) leaves the saved row.

## Documents (v2-sub-create)

`_handle_documents` after the save, item by item: `base64.b64decode` (non-strict), `Document.objects.create(filename=...)` (create
entry indexed through the actor's own association; a collaborator has none), `default_storage.save('<repr(creation
timestamp)>/<document id>/<filename>', bytes)` through the documents-writes upload (`DocumentUploads.save`), then
`attached_membership_documents.add` (no entry). The response lists the attached documents. A later item without its keys is the
400 and the earlier documents stay.

## Pk change and multipart bodies (t2subsa)

- `update` with a different `subscription_membership_id`: `Model.save()` updates nothing and inserts a copy under the new key. The old
  row and its document links stay, the copy has no links of its own (new `documents_data` attach to it), and auditlog logs an update against None, then the creation
  (not an update) for the copy.
- A multipart `add` with at least one file part: DRF merges the files into a mutable copy of the data, so `sport_association` can be
  assigned and the request goes through the form path. A file is read by each field as `str(name)` for the related fields (`“x.txt” is not
  a valid UUID.`) and the price (a file named `12` is the price 12.00), and as an object the other fields reject (`Not a valid string.`,
  the date and datetime format messages). Without a file part the data stays an immutable QueryDict (500).
- A `documents_data` item whose filename the Document column rejects (null, NUL, over 255 characters): `Document.objects.create` raises
  after the membership and the earlier uploads were saved, so the 500 leaves them.

## Not ported (refused before any write)

- Form keys for `documents_data` (the UI sends JSON), and a storage key that already exists (`get_available_name` appends a random suffix,
  not reproducible): 500.

## Evidence

`TESTS/fixtures/subscription_memberships_http.json` (85 cases, generated by `TESTS/fixtures/generate_subscription_memberships_http.py`).

- RED (`red-r3`): Django 85/85; Go 404 on the first case.
- GREEN (`versioned-r1`): 85/85 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with exact membership, document link, audit and index effects.
- Mutation (`mutation-r1`): rolling back the save on a `documents_data` failure fails the first add whose document lacks its keys (CAUGHT).

v2-sub-create: documents_data uploads (88 cases; `add-with-documents`, `add-documents-later-item-missing-key`, `update-with-document`).

- RED (`v2create-red-r2`): Django 88/88; Go 500 on `subscription-memberships-add-with-documents`.
- GREEN (`v2create-versioned-r1`): 88/88 on native Django, native Go, Caddy `/api/v1` and `/api/v2`, with the stored objects.
- Mutation (`v2create-mutation-r1`): skipping the document link (CAUGHT).
