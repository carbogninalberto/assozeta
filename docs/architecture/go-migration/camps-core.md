# Camps and retreats list, add, info, update and delete — implementing with HTTP parity

The five function views of `BE/application/views/camp_and_retreats_views.py`

- `GET camps-and-retreats/list`, `POST camps-and-retreats/add`, `GET camps-and-retreats/<uid>/info`,
  `PATCH camps-and-retreats/<uid>/update`, `DELETE camps-and-retreats/<uid>/delete`

are implemented in Go (`BE_GO/internal/{domain,repository,service,handler}/camps_core.go`,
`BE_GO/internal/dto/camps_core_write.go`) on top of the camps periods code (`camps-periods.md`: request helpers,
field readers, period info rendering and the period delete cascade), including the serializer hook
`CampsAndRetreatsSerializer.save` (a pass-through `super().save()` called by add and update). Registry
`association.campsandretreats.read` (list, info), `.create`, `.update` and `.delete`. Django remains the execution
owner.

## Behaviour

- Each view is `@api_view` with one method (`Allow`: that method and OPTIONS). OPTIONS names the function
  (`Camps And Retreats List`, ...); list, info and add carry their docstrings as the description.
- `is_sport_association(raise_exception=False)` (association owners and collaborators) answers 403 `{'error': 'Only
  sport associations can view|add|update|delete camps or retreats'}`. Info, update and delete run `is_valid_uuid`
  first (400 `["badly formed hexadecimal UUID string"]`); `camps-and-retreats/<str:uid>/info|update|delete` are declared
  before the periods routes, so `camps-and-retreats/periods/info` reaches the camp view with `uid='periods'`.
- List renders `CampsAndRetreatsSerializer` (`camps_and_retreats_id`, `title`, `description`, `sport_association`) for
  the camps of `request.user.sport_association`, without an ordering. A user without an association is an unhandled
  `DoesNotExist` (the HTML 500 page).
- Add looks the association up (none: 500 `{'error': 'SportAssociation matching query does not exist.'}`), then
  assigns its pk into `request.data`, overriding any `sport_association` of the body. That assignment raises for an
  HTML form (`This QueryDict instance is immutable`), a list (`list indices must be integers or slices, not str`) and
  any other non-mapping body (`'<type>' object does not support item assignment`, including `null`): 500
  `{'error': str(e)}`. Validation errors answer 400 `{'error': str(ValidationError)}` (the ErrorDetail dict repr in
  field order: pk with its UniqueValidator, title, description, sport_association). The row is created (audit
  create, indexed `direct`) and saved again without changes (no second entry); 201
  `{'message': 'Camp or retreat added successfully', 'camp_and_retreat': data}`.
- Info: users that are not associations or collaborators get `CampsAndRetreatsPublicInfoSerializer`
  (`sport_association` becomes `{sport_association_id, denomination, username}`); 404 `{'error': 'Camp or retreat not
  found'}`. Others get the camp (404), the association comparison (403 `{'detail': 'You are not allowed to view this
  camp or retreat'}`; no association: 500 `{'error'}`), then `CampsAndRetreatsInfoSerializer` with the periods by
  `start_date` rendered as the period info view renders them.
- Update is partial, after the camp lookup and the association comparison (403 `You are not allowed to update this
  camp or retreat`). A `sport_association` of another tenant (including a deleted association, which the related
  field's default manager still finds) raises the tenant scope's PermissionDenied, re-raised as DRF's 403. Non-mapping
  bodies are 400 `non_field_errors` (`null`: `No data provided`). Only changed fields are audited (indexed `direct`).
- Delete (403 `You are not allowed to delete this camp or retreat`) runs the collector: the camp's periods with their
  services, the subscription periods of those periods and of the camp's subscriptions, the subscriptions, and every
  link row of the deleted periods, services and subscription periods. The camp's entry is indexed through
  `changes_json.sport_association` (also for a collaborator), the cascaded entries through the actor's own
  association (none for a collaborator).

## Not ported (refused with a 500 before any write)

- Add or update with multipart file parts (DRF merges the files into a mutable copy of the form data).

## Evidence

`TESTS/fixtures/camps_core_http.json` (88 cases, generator alongside). RED: Django 88/88, Go 404 on the first case
(`red-r1`). GREEN: 88/88 on native Django, native Go, Caddy `/api/v1` and `/api/v2` with exact camp, period, service,
subscription, subscription period, payment, link, audit and index effects (`versioned-r1`). Mutation controls:
indexing the camp's delete entry through the actor fails `delete-owner-cascade` (`mutation-r1`, index fields);
skipping the camp subscriptions' cascade fails the same case (`mutation-r2`, 500). `camps-periods` (whose period info
rendering moved into a shared helper and whose routes now sit beside `camps-and-retreats/:uid/*`) stays green on all
four transports (`versioned-r1`).

v2-rest-b: `PATCH camps-and-retreats/<uid>/update` with a `camps_and_retreats_id` no row has inserts the copy `Model.save()`
inserts (validated values under the new key; the camp the path names stays). auditlog writes two entries for the copy: the
pre-save update against nothing, indexed from its changes, then the create, indexed directly. A key another camp has is the
unique error (400). Two cases (90 in all); RED is the documented refusal (v2-refusals.md, camps-core row). GREEN
`v2restb-green-r1`: 90/90 on the four transports. Mutation `v2restb-mutation-r1` (the pre-save entry indexed directly) CAUGHT.
