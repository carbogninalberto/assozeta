# Legacy medical S3 keys

`MedicalS3Key` resolves the existing medical upload/cleanup key from creation time,
document UUID, filename and `AWS_LOCATION`. These source paths ignore the stored
`Document.filepath`; this function deliberately does not accept it. Production
`core.settings` resets `STORAGE_DIR` to the empty string and uses the S3 backend.
This function does not resolve disk paths, access a provider, authorize a private
download or make a cleanup candidate safe to delete.

The timestamp matches Python 3.13.2 `datetime.timestamp()` and float text, including
the `.0` suffix, microseconds, negative epochs and scientific notation around zero.
An exact integer-microsecond rational is rounded to float once: converting the
microseconds to float before dividing can change a distant timestamp's final digit
and address a different object. Dates must be within Python's UTC year 1–9999 range
and have microsecond precision; unsupported inputs return a fixed error and no key.
UUID zero remains valid. This use of float represents a legacy timestamp filename,
not a monetary calculation.

The two-stage POSIX/backslash normalization and trailing slash behavior match the
installed `django-storages` S3 backend. Unicode, spaces and percent signs are left
as key characters; HTTP escaping belongs to the future provider client. Location
is an S3 prefix, not a local directory: the legacy-supported `../media` prefix is
preserved. Absolute locations and prefixes the legacy normalizer rejects are errors.

There is one explicit correction: a filename whose final key escapes this
document's timestamp/UUID directory is rejected. The legacy normalizer can accept
`../foreign.pdf`, an absolute filename or backslash traversal and address another
object. Go cannot use those names as authority over a different document. Invalid
UTF-8 and NUL also fail without exposing input in the error. Nested paths that
normalize within the document directory remain supported. This does not check
database references, active uploads, object versions or attachment races.

Run production Python with `TESTS/adapters/django/document_keys.py`. Its 5,045 cases
call the installed S3 backend's actual normalization with synthetic settings and
no network. Authored expected timestamp/path vectors anchor the comparison; 5,000
fixed-seed dates exercise the entire Python date range, including rounding. Explicit
legacy cross-document resolutions are checked and rejected by Go. Ordinary Go
tests also cover submicrosecond/out-of-range dates, malformed names and locations.
The package has no provider calls or mutable shared state. Evidence is under
`quality-reports/go-migration/document-keys/`. This is key resolution evidence,
not S3/MinIO protocol, file deletion or cleanup-task completion.
