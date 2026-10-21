# Login membership-card configuration — implementing

`MembershipCardConfigurations.ForLogin` implements the membership-card portion
of `JWTTokenService.build_login_response`. The admitted account and the association
already selected for response construction are trusted inputs. Association
accounts use their own ID; collaborators use the connected owner's ID. Superusers
and other roles return no configuration without querying the database. This is a
response stage, not credential admission or a public endpoint.

The service begins a read-committed transaction, locks the nondeleted association
by both ID and owner, then reads the lowest configuration UUID with `LIMIT 1`.
Existing duplicate configurations remain intact. Existing values, including JSON
null, SQL NULL, scalars and exact large integers, are returned without writes.
Inactive/deleted owner status does not add a new filter to this stage; complete
login admission separately checks the account, as in Django.

If missing, the service generates a UUIDv4 configuration with approval disabled
and SQL NULL customized template. It writes the configuration, create audit and
association audit index in the same transaction. The response exposes only
`emit_only_on_approval` and `customized_template`, matching Django.

The audit preserves action 0, the model content type, UUID object key, Django model
representation and all four string-pair changes. The JSON field's audit value is
`"null"`, distinct from the record's SQL NULL. The request actor/email, network
metadata and correlation ID remain independent of the logging-in account. The
index uses the configuration's association and resolution path `direct`, even
when the request actor owns another association. Anonymous actors cannot retain
an actor email. Generated UUIDs and timestamps are checked rather than copied.

The repository shares the concrete audit-row/index SQL with username preparation;
services still own change semantics, attribution and transaction boundaries. A
missing content type or failed configuration/audit/index insert cannot return a
successful configuration. PostgreSQL deadlock/serialization conflicts retry the
whole transaction at most three times. Cancellation rolls back pending writes.

Evidence from `TESTS/adapters/django/membership_card_configuration.py`:

- Twenty explicitly expected cases use the actual Django login-response builder
  and audit middleware, followed by the Go service on the same private PostgreSQL.
  Django reads and validates every Go-written configuration/audit/index field.
- Existing configurations (including 2/128/512 duplicates) require two business
  statements; creation requires five. Transaction commands are excluded.
- Thirty-two concurrent Go calls use 67 business statements, create one
  configuration/audit/index, and release a four-connection pool.
- Real denied-INSERT roles, actor/index foreign-key failures, injected failures
  after configuration/audit writes or before commit, and cancellation leave no
  partial Go invariant. Foreign owner/association scopes return no data or writes.
- A real PostgreSQL lock cycle between the configuration association and its audit
  actor forces rollback/retry. The test observes the external lock wait before
  allowing the deferred actor FK check, avoiding goroutine-timing assumptions.
- Every managed table and SQL-NULL distinction match after declared test resets.
  The existing username/audit integration suite also checks the shared SQL helper.

There are explicit coexistence/failure differences, not certified parity:

- Django currently has no association lock or uniqueness constraint for this
  creation. Two synchronized missing lookups create two configurations and audits.
  Go's parent lock coordinates participating Go calls only. It does not guarantee
  single creation with an unmodified Django writer; that cross-runtime boundary
  must be implemented and verified before activation.
- Real denied audit/index INSERTs demonstrate Django autocommit partial effects:
  audit denial retains the configuration and raises; index denial retains the
  configuration/audit and is swallowed. The requested Go atomic invariant rolls
  back all three rows and returns an error. These differences are recorded rather
  than normalized into passing failure-side-effect equivalence.

Full response composition, image processing, instructor/registration augmentation,
final user writes, global HTTP admission and portable login contracts remain open.
The login and audit-index ledger entries remain implementing.
