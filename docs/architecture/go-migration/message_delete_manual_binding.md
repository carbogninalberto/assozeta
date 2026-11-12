# Communication recipe binding after the DELETE correction

Reviewed locally against base `6b92cf3aff133b95d8d9d2702121353c62601d7e`.
This report documents the reviewed application-repository source binding.
It records offline checks, not standalone-manual acceptance, browser execution,
package, release or production acceptance. No companion/manual repository was
accessed or changed.

## Exact source delta

The old pin in `docs/manuale/communication-auth-reference-recipes.mjs` resolves
to the local Git source before `639fa127`; the corrected source at that commit
is byte-identical to the source at the assignment base and in the working tree.
No network or external evidence was needed to establish this relationship.

| Source | Local Git object | SHA-256 of source bytes |
| --- | --- | --- |
| `639fa127^:BE/communications/views.py` | `9b4082a38e2d9bbe732a940aff6f3a6d0308ffca` | `3749d8e76d79f9d57371b67a6a40db3c90013713543dcf2978aa4077a3b9955b` |
| `639fa127:BE/communications/views.py`, also assignment base | `6f6cb9df2f9d9a9857c09c8cbb6b52157a8d4279` | `d9cd631494b3a8d84917c80f246022b2aa1f9ea9f914ce1b95e84d47a3f68d83` |

The complete source diff is one hunk inside `communication_messages_delete`:

```diff
@@ -242,14 +242,14 @@ def communication_messages_delete(request, message_id):
     request.user.is_sport_association()
     is_valid_uuid(message_id)

-    # get the message and its message transactions
-    message = Message.objects.filter(
-        sport_association=request.user.sport_association,
-        message_id=message_id,
-    ).first()
-    if not message:
-        return Response({'msg': 'message not found.'}, status=status.HTTP_404_NOT_FOUND)
     with atomic_audit_mutation():
+        # Serialize lookup and deletion so a concurrent loser observes absence.
+        message = Message.objects.select_for_update().filter(
+            sport_association=request.user.sport_association,
+            message_id=message_id,
+        ).first()
+        if not message:
+            return Response({'msg': 'message not found.'}, status=status.HTTP_404_NOT_FOUND)
         message.delete()

     return Response({'msg': 'message deleted.'}, status.HTTP_200_OK)
```

The scoped lookup and absence decision move into the strict audit transaction,
and the lookup acquires a row lock. Authentication, association resolution,
UUID validation, association/message predicates, delete call and response bodies
remain unchanged. Under PostgreSQL READ COMMITTED the competing deletion can
observe absence after the winner commits, preventing a second success/audit.
The absent path now also enters the transaction. This is a real behavior change
in DELETE; it is not a reason to discard source validation.

The parent's PostgreSQL RED/GREEN is documented in
[message_delete_concurrency_reference.md](message_delete_concurrency_reference.md#parent-executed-verification).
The later native Django/Go HTTP verification is documented in
[message_mutation_concurrency.md](message_mutation_concurrency.md#parent-verification-of-the-integrated-candidate).
Those are parent-recorded results, not tests rerun or private receipts inspected
by this assignment. No services, credentials or private pickles were used.

## Review of every excerpt and consumer

The recipe binds the entire file, but cites exactly five excerpts from it.
Each symbol occurs once in both source versions. All five extracted texts and
their line ranges are identical before and after the correction. The ranges
below are inclusive, extracted from the matching symbol for the declared number
of lines. Excerpt hashes use LF-joined lines without adding a terminal newline.

| Contract | Symbol in `BE/communications/views.py` | Lines; declared length | Reviewed claim and unchanged source support |
| --- | --- | --- | --- |
| `post_api` | `def configuration_send_post(request):` | 150–172; 23 | Validates `PostSerializer`, creates an `INSIDE_APP` message and one association-recipient transaction, returns `post sent.`. Supports the prepared Post procedure; does not prove athlete-side visibility. |
| `email_api` | `def configuration_send_email(request):` | 88–147; 60 | Reads an existing message or creates an EMAIL message, validates input, splits recipients, creates transactions, calls `configuration.send_email`, returns its `msg` and `result`. Describes the send path; SMTP delivery and receipt remain external. |
| `email_logs` | `def communication_email_logs_list(request):` | 65–85; 21 | Filters association logs to 90 days, orders by `sent_at`, serializes results, returns today's count, total count and results. Supports log/limit cautions, without proving recipient delivery. |
| `workflow_update` | `def communication_workflows_update(request, workflow_id):` | 330–370; 41 | Resolves the association/UUID, loads a scoped workflow, validates/saves a partial update, and calls `workflow.trigger()` only on disabled-to-enabled transition, with the existing cron-trigger reset. Describes toggles and updates; does not prove scheduling or delivery. |
| `messages_write_api` | `def communication_messages_add(request):` | 260–278; 19 | Validates `MessageInputSerializer`, saves with the request association inside `atomic_audit_mutation()`, returns `message added.`. The declared excerpt also includes the following list-view decorators/signature. Supports the prepared save procedure; reopening/persistence still needs the workflow evidence. |

Exact excerpt SHA-256 values:

```text
post_api           fbcfed9c28a1d4e93992d385709d40c017030538906b4531c64248e62f76c0f1
email_api          5baa0297deedecccd7ec9d47d0a0c5ac141dfc3ca78e530dd1657df47ce7969e
email_logs         8945023f3639dbfc0e94da30885381fe01e3f25aaf6699714d17e04dc228ddb4
workflow_update    1f8ed6b619680f0dce60e06977dab742b73126f448119c31f6579dd08aa716e9
messages_write_api 294583408f5aeb0219a993bba7a204f9f0e0abb175f9f59aa39bfd55a5b7223a
```

These are all nine section consumers, with their existing Italian titles and
statuses preserved. The page prefixes are defined immediately below the table.

| Page | Section ID | Italian title | Contracts from this file | Status |
| --- | --- | --- | --- | --- |
| C | `post` | Post | `post_api` | `pending` |
| C | `messaggi` | Messaggi | `email_api`, `messages_write_api` | `pending` |
| C | `come-inviare-una-comunicazione-sms-email` | Come inviare una comunicazione SMS/email? | `email_api`, `email_logs` | `needs_external_verification` |
| C | `limiti-delle-comunicazioni` | Limiti delle comunicazioni | `email_logs` | `needs_external_verification` |
| F | `note-e-consigli` | Note e consigli | `email_logs` | `needs_external_verification` |
| T | `informazioni-importanti` | Informazioni importanti | `email_logs` | `needs_external_verification` |
| T | `attivare-disattivare-e-modificare-i-workflow` | Attivare, disattivare e modificare i workflow | `workflow_update` | `verified` |
| T | `attivare-un-workflow` | Attivare un workflow | `workflow_update` | `pending` |
| T | `disattivare-un-workflow` | Disattivare un workflow | `workflow_update` | `pending` |

C = `docs/comunicazioni.mdx`; F =
`faq/come-inviare-email-nuovi-iscritti.mdx`; T =
`tutorials/come-impostare-automazioni-workflow.mdx`.

The verified workflow overview remains a textual source reference only:
“Descrizione testuale verificata nelle sorgenti; non attesta una procedura
eseguita in browser.” All other Italian reasons, titles, intents and contract
memberships are also unchanged. No behavior prose change is warranted by this
delta. Authored MDX remains the only prose source; its companion repository was
outside the review boundary.

The builder eagerly validates all 62 contracts before returning its 47 sections,
so this stale file pin blocked the whole family, including unrelated auth
sections. The update changes one pin and adds a review comment; it changes no
contract, validation branch, status or procedure. Exact equality checks against
the base recipe confirmed all exported descriptors, keys and contracts remain
unchanged, and only the reviewed views hash differs in the source map.

## Offline RED/GREEN and rejection checks

Recorded execution used already installed Node v22.19.0 and Python 3.13.16.
No installs were performed. Commands ran in the isolated assignment tree. The
portable forms below run from the repository root with that Node selected by
`PATH` and `PRODUCTION_PYTHON` set to the corresponding installed Python executable.

Before editing the recipe, the recorded command exited 1 after 8 tests:
5 passed and 3 errored.

```sh
: "${PRODUCTION_PYTHON:?Set PRODUCTION_PYTHON to the installed production Python}"
PYTHONDONTWRITEBYTECODE=1 "$PRODUCTION_PYTHON" -m unittest docs.scripts.tests.test_manuale_audit_dependencies docs.scripts.tests.test_manuale_authored -v
```

Failures were:

- `test_capture_sources_and_reference_validation_include_audit_dependency`:
  `Communication/auth source evidence changed; review required: BE/communications/views.py`.
- `test_complete_legacy_bindings_use_actual_reviewed_sources`: its Node
  subprocess exited 1 while building authored references.
- `test_coverage_includes_text_references_without_promoting_prepared_procedures`:
  its Node catalogue subprocess exited 1.

After the reviewed pin change, the same two suites plus existing recipe
validation tests exited 0: **12 tests passed**.

```sh
PYTHONDONTWRITEBYTECODE=1 "$PRODUCTION_PYTHON" -m unittest docs.scripts.tests.test_manuale_audit_dependencies docs.scripts.tests.test_manuale_authored docs.scripts.tests.test_manuale_references -v
```

Additional in-memory Node assertions passed without changing any source file:

- Importing the historical recipe directly from the base Git object still
  rejects the corrected views with the exact source-review error above.
- The new recipe accepts all current sources and all five historical excerpts
  compare exactly with their corrected counterparts, including line positions.
- Substituting the previous views, removing `select_for_update()`, changing
  `post sent.` to `post changed.`, or appending an uncited comment each rejects
  the views binding. Symbols remain present in every variant.
- Substituting a zero canonical hash for each of the **24** reviewed paths
  individually rejects that exact path. Whole-file validation is retained.
- CRLF input canonicalizes successfully using the existing source-reader rule.
- The full local reference catalogue resolves **486** unique section identities.
  The communication/auth family retains **18 pending, 10 external, 5 unsupported
  and 14 textual verified references**. Its 47 editorial descriptors all retain
  `verified: false`.

The first extra diagnostic incorrectly imposed symbol uniqueness on every
catalogue excerpt and failed on the existing repeated `part.kind === 'cards'`
in `UI/src/routes/manuale/ManualContent.svelte`. Uniqueness was restricted to
the five reviewed function definitions; the complete diagnostic then passed.
No shared source or recipe was edited to address that diagnostic assumption.

No new regression file was added: no claim or executable validator changed,
and the existing tests already reproduce the stale-binding failure, pass with
the reviewed binding, reject source/recipe mutations and preserve unverified
procedure/publication gates. The parent retains independent checks/integration.

## Remaining binding and acceptance limits

- This recipe has **no DELETE excerpt or concurrency claim**. Whole-file source
  sealing detects further DELETE changes, but does not prove concurrent
  deletion. The parent-recorded PostgreSQL/native HTTP evidence remains separate.
- Existing authored communication workflows list the views among their source
  dependencies. This update does not refresh captured source hashes or establish
  validity/reuse of prior browser captures. No captured outputs were changed.
- A recipe change also changes its recipe hash. Companion MDX/sidecar recipe
  bindings have not been read, refreshed or published; their review and any
  standalone-manual validation require the separately approved parent work.
  A successful application-repository catalogue build cannot replace that gate.
- No source, helper, ledger, shared recipe, workflow or companion evidence was
  edited. Delivery, scheduling, recovery, browser, package, release, production
  and complete migration acceptance are outside these offline results.

Changed deliverables: `docs/manuale/communication-auth-reference-recipes.mjs`
and this report, plus the explicitly requested local coordination notes
`quality-reports/coordinator-inbox.md` and
`quality-reports/message-manual-progress.md`. No commits, pushes, publication,
network, apps, browser or nested agents were used. **Standalone-manual
acceptance is explicitly not granted.**
