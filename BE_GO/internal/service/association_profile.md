# Association login profile — implementing

`AssociationProfiles.Read` provides the 50-field `SportAssociationSerializer`
response for a trusted association-owner ID. Login composition must resolve that
owner from the selected account or its collaborator relationship. This component
is not a public profile endpoint and does not grant tenant access from a client ID.

The repository applies the association's nondeleted manager predicate and joins
the owner's `date_joined` without filtering inactive/deleted owners: Django uses
the related User base manager for that read. The service rejects a mismatched
owner or a group from another association. Missing/deleted associations return no
profile. A subsequent read immediately observes a deletion or restoration.

All scalar, nullable, JSON and template fields are explicit DTO members. Existing
custom template names are returned as stored, without calling the model's template
fallback helpers. JSON scalars and integers beyond binary floating-point precision
are preserved. Subscription/membership fees are selected as PostgreSQL numeric
text and serialized as strings (or null for the nullable membership fee). No
floating-point monetary conversion or arithmetic occurs. Financial calculations,
Go financial writes and broader non-finite/historical amount behavior remain
separate acceptance work.

`reviewed` is true for owners joined strictly less than five elapsed days ago;
otherwise it retains the persisted value. This uses the owner's timestamp, not
the logging-in collaborator's. Avatars/logos/signatures are unchanged here.
Image processing is a later login-response augmentation stage.

Two business SELECTs, after the shared transaction fence, fetch the association
inside a read-only repeatable-read transaction
and its groups; missing associations require one. Group lookup is constrained by
the selected association ID and follows Django's unsorted query and projection.
No name/UUID ordering, truncation or pagination is invented. Memory is proportional
to the required response, including all groups; it does not load foreign groups
or issue per-group queries. It is not a constant-memory export implementation.
A group-query failure propagates, unlike optional user onboarding; rollback
releases an aborted PostgreSQL transaction before the connection is reused.

The account adapter runs 23 explicit expected JSON/outcome cases against the
actual Django serializer/DRF renderer and Go over the same private PostgreSQL.
They cover all 50 fields, current/deleted/inactive owners, missing/deleted
associations, the exact five-day/microsecond boundary, positive/negative numeric
limits and PostgreSQL scale rounding, nullable/scalar JSON, custom templates and
0/1/16/128/512 groups. A real role with denied group SELECT reproduces the required
failure in both runtimes. Query counters assert two/three SELECTs including the shared fence; 32 concurrent
512-group reads use 96 SELECTs and release the four-connection pool. Unit tests
reject foreign owner/group data and cancellation. All managed tables match after
the declared deletion/restoration test transition.

These are backend-specific response-component contracts. Full login composition,
collaborator/instructor augmentation, image processing, registration settings,
membership-card configuration composition, final user writes and portable HTTP
login contracts remain required. The login ledger row stays implementing.
