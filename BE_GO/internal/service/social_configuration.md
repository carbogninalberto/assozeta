# Current social client IDs

`SocialClientIDs` implements the current-instance resolution used by Django's
`instance.integration_configuration.provider_client_id` for Google and Apple.
Its repository reads only `self_hosted`, `integration_settings` and
`display_settings` from the first `instance_instanceconfiguration` row, ordered
by primary key. Each new operation performs one query and releases the connection
before returning. No configuration is cached across operations, and a database
failure never enables an environment fallback.

Environment client IDs are explicit constructor inputs. With no instance, or a
non-self-hosted instance, those values are effective. Self-hosted instances use a
non-null provider `settings` override even when it is empty or malformed. Missing
or null overrides restore the environment value. Disabled overrides return an
empty client ID without requiring a saved ID. An enabled override retains the
original JSON type of `client_id`; the eventual verifier must apply its own
audience rules. Legacy `google_client_id` and `apple_client_id` columns are not
effective overrides in current Django and are not read by Go.

Only Google's environment fallback observes `login.allowOauthLogin`. The default
is true, including an absent `login` object or missing flag after the model's
shallow nested merge. Explicit Google overrides and Apple ignore that visibility
setting. JSON truthiness is retained; for example a nonempty string `"false"` is
true. Malformed stored structures return a generic configuration error without
including their contents. They cannot silently fall back to environment values.

The private accounts adapter declares 33 expected outcomes before exercising
either implementation. Django exception classes are explicit. It tests live
override changes, disable/reset, both providers, visibility, hosted mode, ignored
legacy columns, retained null/list/number IDs and malformed structures. Go uses
the same repository and service instance across updates, proving the next call
observes the new row. All managed Django tables are read back after restoring the
declared fixture transitions.

At 1, 16, 128 and 512 instance rows, the lowest primary key remains authoritative
and each operation issues exactly one SELECT. Each size also runs 32 concurrent
reads with a four-connection pool. A real PostgreSQL table lock checks cancellation
of a pending query and subsequent pool reuse. An absent instance, canceled entry
and closed database are tested separately. These fixtures bypass the model's
singleton write guard only to characterize historical multiple-row reads.

This is a read-only resolution component. Environment-loader/composition-root
wiring, actual Google/Apple cryptographic and network verification, the integration
administration endpoints, changes during an already admitted provider request,
and complete public login remain open. No Google or Apple endpoint is contacted
by these tests. This does not cover AI/Stripe encrypted settings or claim that
all malformed historical JSON forms are supported.
