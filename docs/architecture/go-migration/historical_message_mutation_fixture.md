# Historical saved-message mutation fixture

This is an offline, synthetic fixture and derivation API. It closes the empty
saved-message sections in the immutable historical base without exporting current
models. Unit validation is **not** a successful live historical import, HTTP
mutation result, replacement restore result, or full restore acceptance. The
parent owns those executions and their observations.

The only archive operations append to `data/52_messages.json` and
`data/53_message_transactions.json`. Every base ID, existing row, section order,
manifest property other than counts, account, legacy URL, and attachment byte is
preserved. The helper does not compose or change the existing read-route overlay;
it derives directly from `bakney-v1.json`. Its fixed two-section overlay cannot
use the read-route helper's User/CollaborationInvites-specific validation API, so
it follows that helper's canonical JSON and deterministic ZIP style independently.

## Binding and inspected historical source

The source was inspected using `git show` against the exact commit in the
historical source repository's object database, never by reading its working-tree
files. For the portable command form, set `HISTORICAL_SOURCE_ROOT` to a local
checkout containing that pinned Git object:

```sh
: "${HISTORICAL_SOURCE_ROOT:?Set HISTORICAL_SOURCE_ROOT to the historical source checkout}"
git -C "$HISTORICAL_SOURCE_ROOT" \
  show 0058de5909734bdd0f00867abbdb723649de4238:communications/models.py
```

The same pinned-object procedure was used for all six files below. SHA-256 means
hashing the exact raw `git show` bytes, without decoding/reformatting first.

| Inspected path | SHA-256 |
| --- | --- |
| `communications/models.py` | `138897672e8aa1185c238859dd1f6e2b39d4f6d44921a4f0a7168efecc346b65` |
| `communications/serializers.py` | `9e35179af12182558578b880489bbc77827b2cf3fbc877af596d3b2756ecec7f` |
| `communications/views.py` | `961b41ee8077f8bfabc0fa3d787ecef62223469d219fb3cc96a5c913c64bccbe` |
| `application/services/export_service.py` | `79d5a8c5df1160c101f7113ad8418bbd393ae250615335381f6d4b02e85032f2` |
| `application/services/import_service.py` | `5d91f2b366f80a5d807483fee4476236b00ae7db20fe87b7d49dad2eda4c07b2` |
| `application/models/user_models.py` | `43c96210095dc1444d1dfffb71b18e3676190badcbe9711223a8cf1173fe3a1c` |

`user_models.py` defines both User and SportAssociation, including the required
owner relationship. The source repository is
`carbogninalberto/django-bakney-sport`, revision
`0058de5909734bdd0f00867abbdb723649de4238`. Runtime validation binds the recorded
hashes to constants; it does not require a checkout of that repository or read
historical source files at import time.

| Fixed artifact/control | Value |
| --- | --- |
| Base JSON bytes | `15420` |
| Base JSON SHA-256 | `0d0f5d58946de1306d93476261a6dc6fe09cb56c3b5e23b4d3dcd2c4e75f6713` |
| Base model sections / JSON entries | `67` / `68` |
| Overlay SHA-256 | `03ac87a1f4209f024dfd92d77e549e8aef6dfc86c4324aca5c7800c9756a1df4` |
| Derived ZIP SHA-256 | `e0a2e7ab8b277944980cf284fbc31fb166fba2bd02d902053be6c8dd7f2505ea` |
| Derived ZIP bytes / members | `19585` / `69` |
| Full contract canonical SHA-256 | `4bd203906fbeb061bc43febab27671e14ffcb78b6def8af3f3d5f7ced4e425a3` |

Overlay and full-contract hashes use UTF-8 JSON with `sort_keys=True`,
`ensure_ascii=False`, separators `(',', ':')`, and no non-finite numbers. The
ZIP uses base entry order, canonical JSON members, the unchanged UTF-8 text
attachment, stored compression, `1980-01-01 00:00:00`, Unix creator metadata,
regular-file mode `0644`, and no extra fields or comments. Re-signing edited
records cannot turn this immutable specimen into a different valid specimen.

## Observed schema and synthetic records

Every new UUID, recipient, message, and association identity is synthetic; the
records were not observed in a customer database or copied from a real export.
The schema, export section names, foreign-key names, choices, and nullable
fields come from the inspected historical source. Expected rows/counts are
separately authored literals in the new JSON, with independent test assertions;
no application, model, serializer, or current exporter generates the oracle.

`Message` has exactly `message_id`, `sport_association_id`, `type`, `message`,
`subject`, and `created_at` in this authored overlay. The historical choices are
`EMAIL`, `SMS`, and `INSIDE_APP`. A wall post is `INSIDE_APP`, not `POST`; SMS
provides the supported historical type outside the email/post pair.

`subject` genuinely permits both null and empty strings. `message` is a non-null
TextField: the fixture includes an empty stored SMS body, which direct model
save/import can store, even though historical send serializers reject an empty
body. A null body is unsupported and deliberately rejected. Source timestamps
are fixed UTC strings, with a null `created_at` on the wall post to exercise the
historically nullable source field.

Delivery children are actual `MessageTransaction` records with
`message_transaction_id`, `message_id`, `recipient`, `sent_on`, `delivered_on`,
`opened_on`, `clicked_on`, `bounced_on`, `bounced_reason`, `bounced_type`, and
`bounced_status`. There is no invented transaction `created_at` or association
field. Deliveries cover delivered/opened/clicked timestamps, a bounced record,
and distinct null/empty bounce metadata. The post recipient is the owning
association UUID string, as in the pinned `configuration_send_post` view.

All new IDs use prefix `b2600000-0000-4000-8000-`:

| Role | UUID suffix | Children |
| --- | --- | --- |
| Retained email | `000000000101` | `000000000201` |
| Retained wall post | `000000000102` | `000000000202` |
| Retained SMS, empty body/subject | `000000000103` | `000000000203` |
| Delete target email | `000000000104` | `000000000204`, `000000000205` |
| Separate foreign owner | `000000000301` | Owns association below |
| Separate foreign association | `000000000302` | Owns message below |
| Separate foreign email | `000000000303` | `000000000304` |

The own association and owner keep their existing base UUIDs
`7616fd26-589f-5551-b17f-0478e305434d` and
`f4b360ff-0fa7-58a2-bf1b-f2016dba1fab`. No canonical message-mutation IDs or
fixtures are changed.

## Public API

Import `TESTS.fixtures.historical_message_mutations`. Every operation is local,
standard-library-only; importing the module has no fixture I/O or external side
effects. Returned objects are fresh mutable copies of fixed validated data.

| Function | Result |
| --- | --- |
| `load_contract(path=None)` | Load default new JSON, or an explicit local copy, then validate all bindings. |
| `validate_contract(contract)` | Validate an already-loaded object and return a detached copy; raise `FixtureError` on malformed/tampered data. |
| `derived_archive(contract=None)` | Complete base-shaped object, including all entries, manifest, account, base IDs, and attachment text; appended message/delivery sections and recalculated counts. |
| `archive_bytes(archive=None)` | Deterministic ZIP suitable as the real importer input; reject a supplied object differing from the fixed derivation. |
| `expected_projection(stage='imported', contract=None)` | Explicit source rows, exact identity/count expectations, foreign contrast graph, and timestamp observation policy. Also accepts `stage='after_delete'`. |
| `foreign_sentinel(contract=None)` | Separate complete User → SportAssociation → Message → MessageTransaction setup graph in dependency order. |

For example, the parent can obtain input and controls without importing any app:

```python
from TESTS.fixtures import historical_message_mutations as fixture

contract = fixture.load_contract()
archive = fixture.derived_archive(contract)
zip_payload = fixture.archive_bytes(archive)
initial = fixture.expected_projection('imported', contract)
after_delete = fixture.expected_projection('after_delete', contract)
foreign_setup = fixture.foreign_sentinel(contract)
```

The full archive object retains **base** `ids`, matching the existing fixture
shape; the expanded message/sentinel identity inventory is `contract['ids']`.
There is no `setup_token`, runtime adapter, HTTP execution, database connection,
current-model import, or automatic foreign-row installation in this API.

## Parent actual-import setup and checks

The supported importer requires exactly one SportAssociation, and the pinned
historical exporter scopes message transactions through message association.
Appending a foreign association to the ZIP would make an invalid input. The
coordinator accepted the separate setup graph, which contains all four required
rows and references and is intentionally absent from the ZIP.

1. Use an exclusively owned disposable runtime and the established supported
   import/setup path, with the full derived ZIP as input. Keep the usual actual
   migrations, worker, task completion, errors/warnings, attachment, and
   population checks. The archive has one owner/association, four Message rows,
   and five MessageTransaction rows. All 67 model counts remain independently
   listed in `expected_counts`. Skip exactly one BillingPlan and one
   PreviewAndCustomFeatures; unknown/malformed sections and other skips remain
   errors. This worker has not run any of these runtime steps.
2. Verify own message and delivery primary-key sets against the explicit
   `imported` projection, resolve every delivery to the expected message and
   every message to the original base association, and check every ordinary
   field exactly. Decode raw database values for comparison; the pinned
   MessageSerializer escapes message/subject on reads, so raw source rows are
   not an authored HTTP-response oracle.
3. Characterize `timestamp_policy` explicitly. The source model marks
   `Message.created_at` and `MessageTransaction.sent_on` as `auto_now_add`; the
   importer constructs and saves new objects. Its current behavior predicts non-null timezone-aware
   timestamps within the actual import start/end window for these two fields,
   including the post with null source `created_at`. Record the resulting
   values. All other delivery timestamps must match their fixed source values.
   Do not overwrite imported timestamps to manufacture equality with source.
   This is a characterization of a potential importer defect, not an allowed
   normalization for migration acceptance. Independently compare all timestamps
   with the fixed source values and keep historical preservation unverified when
   they differ. A bounded reference correction needs actual import evidence.
4. Install the separately returned foreign rows using the parent-owned runtime
   setup path in dependency order. The foreign owner has an unusable password
   (`!`); it is an isolation sentinel, not a supplied login account. Record the
   resulting entire foreign graph as a baseline, including timestamps. No
   message send endpoint or delivery service is needed to install these
   synthetic persisted setup rows. The helper itself does not install them.
5. With that setup present, global message/delivery totals are `5`/`6`.
   A successful authorized deletion of `contract['mutation']['delete_message_id']`
   must remove exactly the two named child UUIDs and the target UUID. Compare
   retained rows and links against `after_delete`: own totals `3`/`3`, global
   totals `4`/`4`. Retained automatic timestamps equal the observed import
   baseline (`unchanged_from_import`); the entire foreign graph equals its
   observed parent-setup baseline (`parent_setup_baseline`). Runtime HTTP denial,
   audit/index, cache, transaction rollback, create, and restore behavior still
   require the parent's independent actual execution and assertions.

The `messages`/`deliveries` projection rows contain **source** values;
`timestamp_policy` predicts the current importer's two automatic-field overwrites.
`expected_projection` explicitly reports `acceptance_scope` as
`current-importer-characterization`, `historical_timestamp_preservation_verified`
as false, and the preservation requirement as `exact-source-values`. An observed
import-window value does not satisfy that requirement. `foreign_sentinel` contains
source/setup values, with an observed parent-setup baseline policy.
`combined_counts` apply only when the parent actually installs that graph.

Replacement restore may intentionally remove another association because of
instance-wide replacement semantics. Retention of the separately installed
foreign graph across restore is **not established** here and must not be assumed
from the mutation projection. The parent must determine the applicable restore
scope and acceptance expectations before using this contrast for restore.

The pinned message-detail view orders transactions by `-created_at`, although
that historical transaction model has no such field. This fixture does not
invent it or repair the reference; detail-route behavior requires parent review.

## Focused offline verification

Tests were written before the helper/JSON implementation. Portable form of the
initial RED command, from the repository root with `PRODUCTION_PYTHON` set to the
already installed Python 3.13.16 executable:

```sh
: "${PRODUCTION_PYTHON:?Set PRODUCTION_PYTHON to the installed production Python}"
"$PRODUCTION_PYTHON" -m unittest TESTS.test_historical_message_mutation_fixture -v
```

It exited `1` with a loader ImportError for the missing new helper (one loader
error). After implementation, the same production-Python command passed all
18 tests. They check immutable base bytes and records, complete manifest counts,
exact skip counts, explicit values/identities/relationships, target cascade
membership, foreign swaps, nullable/empty distinctions, malformed nested shapes,
unknown sections/fields, duplicate identities and JSON keys, re-signed tampering,
fixed source/overlay/archive binding, supplied-archive tampering, deterministic
ZIP bytes/metadata, detached return values, and import/use with application
imports, subprocess actions, and socket actions blocked in a fresh `-S` Python.

These results establish fixture controls only. No services, database imports,
HTTP requests, installations, builds, agents, commits, pushes, or publication
were performed for this bounded assignment.

## Subsequent parent correction

The importer-overwrite discussion above records the baseline inspected when this
immutable specimen was authored. The parent subsequently reproduced and corrected
that defect for Message.created_at and MessageTransaction.sent_on. See
[message_import_timestamps.md](message_import_timestamps.md) for exact-source
PostgreSQL tests. The fixture and its original characterization metadata remain
unchanged; import-window values must not be accepted in place of source values.
