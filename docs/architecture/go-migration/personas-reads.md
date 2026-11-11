# Personas reads — implementing with HTTP parity

`GET personas/list`, `personas/all`, `personas/all-tutors`,
`personas/all-with-subscriptions` and `personas/<str:pk>/info`
(`AssociateViewSet` list, all, all_tutors, all_with_subscriptions and info in
`BE/application/views/personas_views.py`) are implemented in Go
(`BE_GO/internal/{domain,dto,repository,service,handler}/personas_reads.go`).
Django remains the production execution owner.

## Behavior

- **Identity:** the viewset's `IsAuthenticated`; the registry maps list, all,
  all-tutors and info to `association.personas.read` and has no entry for
  all-with-subscriptions (restricted collaborators get the unmapped-endpoint
  403); administrators need impersonation; the viewset is not an athlete view.
  Every action reads `request.user.sport_association` (the User cached
  property): athletes get `403 {"detail": "User is not a sport association"}`,
  an owner whose association is missing or soft-deleted a 500. Collaborators
  act as the owner.
- **Methods:** the GET mapping adds HEAD and the class defines `delete()`, so
  Allow is `GET, HEAD, DELETE, OPTIONS` on every route; other methods are 405.
  OPTIONS is `Associate` with an empty description (no class docstring).
- **get_queryset** (list, info, and DELETE through `get_object`): the
  association's live associates through the soft-delete, group-aware manager
  (`X-Group-Id`; a malformed id is ignored), annotated with
  `is_tutor_annotated` (any relation naming the row as tutor) and
  `tutors_count` (`Count('tutor_relations')`), then:
  - `query[generalSearch]` (`smart_search` → `sqlite_search`): the term is
    stripped and lowercased; each `\w+` word must `icontains`-match one of the
    31 concrete non-relation fields (`field::text`: UUIDs, dates, booleans as
    `true`/`false`, the creation timestamp in UTC) or a tutor's first/last name,
    tax code, email or phone; numeric words also match the date variations of
    the term (`dd.mm.yy[yy]` and the `/`, `-` forms, ISO, month/day swaps;
    two-digit years are 20xx up to the current year's two digits, else 19xx)
    on `born_date`/`creation_date`; with several words the reversed and
    original word sequence may match a name, address, city or notes field.
    `%`, `_` and `\` are escaped. The tutor search is a second relation join
    after the Count annotation, so `tutors_count` then counts relation ×
    matching-tutor rows (an associate matching on its own fields with two
    tutors counts 4). The rows are ordered by a relevance CASE (first name,
    last name, tax code: 10 exact, 7 prefix, 4 contained, else 1) and distinct.
  - `query[is_tutor]` (non-empty) through `BooleanField.to_python` (`t`,
    `True`, `1`, `f`, `False`, `0`; anything else is a 500);
    `query[type]` comma tokens `athletes`/`tutors` (both or neither: no filter);
    both filters apply together.
  - `query[minAge]`/`query[maxAge]` (present, even empty): `born_date` against
    `today.replace(year=today.year - int(value))`; an invalid integer, a year
    outside 1..9999 or 29 February into a common year is a 500.
  - `sort[field]` with a non-empty `sort[sort]`: `tutors` → `tutors_count`,
    `age` → `born_date` reversed, `full_name` → `last_name`; `asc` ascends,
    anything else descends; `?` (ascending only) is random; one leading `-` is
    stripped and the name must resolve (else a FieldError 500). Without a sort
    the rows are unordered (or by relevance when searched).
- **list:** `KTDatatablePagination` (`pagination[perpage]` a strictly positive
  `int()`, else 10, no maximum; `pagination[page]` absent or empty is 1,
  `last` the last page, else `int()` within the pages, otherwise
  `404 {"detail": "Invalid page."}`) and `AssociateSearchSerializer`
  (`is_tutor` from the annotation, `age` = `int(days / 365.25)` on
  `date.today()`, `tutors` from the prefetched relations whose tutors use the
  base manager, so soft-deleted tutors are listed). `meta` is
  `{total, page, pages, perpage (the page size used), rowIds}`.
- **all / all-tutors:** the association's live associates (group-aware),
  unordered, as `{"data": [{associate_id, first_name, last_name}]}` and as a
  bare list; query parameters are ignored.
- **all-with-subscriptions:** the association's subscriptions that are not
  deleted or archived, with a live associate and `start_date <= today <=
  end_date` (group-aware on the subscription), as `{"data": {associate_id:
  {subscription_id, associate_id, first_name, last_name, email, tax_code,
  subscription_status, type}}}`; a later row for the same associate replaces
  the value.
- **info:** `get_object()` runs the full queryset (its parameters apply: a
  filtered-out or searched-out row is a 404, a parameter error a 500), then
  the pk: a value the UUID field rejects is `404 {"detail": "Not found."}`, a
  miss (foreign, soft-deleted, other group) `No Associate matches the given
  query.`. `AssociateSerializer` renders the declared fields (`full_name`
  with `None` for a missing name, `is_minor_now`, `is_tutor`, `age`,
  `family_members` through the live, group-aware manager, `tutors` as
  `{tutor: BasicAssociateSerializer, is_primary}`), then the model fields and
  forward relations; `tutors_data` (write-only) and `incomplete` (no
  attribute) are omitted. The nested serializers' `tutors` are the M2M manager
  (live and group-aware) two levels down. Every list is unordered.
- **DELETE:** on the routes without a pk, `get_queryset` (athletes 403) and
  then `get_object`'s lookup-kwarg assertion (500).

## Not ported (refused with the 500 page, never a guessed body)

- (DELETE on `personas/<pk>/info` once the row is found is the soft delete
  ported with the personas-writes family, `personas-writes.md`.)
- (Ported in tailpay.) `query[generalSearch]` terms beyond Latin: the term is decoded with `replace` for malformed bytes (one U+FFFD per
  maximal ill-formed subpart), stripped, lower-cased (U+0130 becomes `i` plus U+0307, the final sigma rule) and split on `\w+`; every
  code point's `lower()` and `\w` class was compared with CPython 3.13 (Go's tables lack only CJK Extension I, added as word characters).
- `sort[field]` names that join other tables: relation paths (`a__b`), the
  `tutors` M2M and reverse accessors (Django would order by them).

Not authored: HEAD bodies, two current subscriptions for one associate (the
surviving value follows PostgreSQL's row order in both), age boundaries on the
run date. The age-filter cases hold for run dates from 2026-11 to 2030-03; ages
and `is_minor_now` are resolved by the contract on the run date.

## Go implementation

list issues three queries (count, page, relations), info five (row, relations,
two M2M levels, family). The search SQL mirrors Django's joins (including the
second tutor join that inflates `tutors_count`); relevance parameters are bound
only when the ordering uses them. Python semantics live in
`service/personas_reads.go` (`int()`, `strip()`/`lower()`, `\w+`, the date
regexes, `date.replace`) with unit tests.

## Evidence (TDD)

`TESTS/fixtures/personas_reads_http.json` (generator alongside; contract
`TESTS/contracts/test_personas_reads_http.py`, adapter
`TESTS/adapters/django/personas_reads_http.py`) authors 201 cases over nine
associates (own, grouped, soft-deleted, foreign, nameless, without a born
date), ten tutor relations, two families and ten subscriptions. The Django
reference corrected one expectation, confirmed in Django's compiler: a random
ordering keeps working with `distinct()` (the ordering is added to the select
list); a braced-UUID info case was dropped because the harness attributes
requests by their decoded path. RED: Django 201/201 and Go 404 on the first
case before any Go code. GREEN: 201/201 on native Django, native Go, Caddy
`/api/v1` and `/api/v2` with the model state unchanged. Counting distinct
relations for `tutors_count` (ignoring the search join) fails
`personas-reads-list-search-tutors-count-join`.

## Ported in taildocs

`query[generalSearch]` now covers every script: `dto.PythonLower` is `str.lower()` (simple mapping, U+0130 as `i` plus a
combining dot, the final sigma rule), `dto.PythonUTF8Replace` decodes malformed bytes as Python does with
`errors='replace'`, and the date patterns read any decimal digit as `\d`. Goldens come from Python 3.13; Go's Unicode
tables are 15.0 against Python's 15.1. The case data is stored in lower case so the result does not depend on the
database locale.
