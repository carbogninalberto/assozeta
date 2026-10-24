# Historical Argon2 verification

This internal compatibility adapter is based on the generic Go Authors Argon2
implementation in `golang.org/x/crypto v0.46.0`. Original file hashes are recorded
in `upstream.json`; the upstream BSD license is retained in `LICENSE` and source
headers. The normal Argon2i/Argon2id v19 path continues to use the unmodified
upstream module directly.

Django's Argon2 verifier also accepts v16 and Argon2d, which the upstream public
Go API does not expose. The limited changes here are:

- Export a single internal `DeriveKey` with explicit mode and version.
- Include the selected version in the initial hash.
- Overwrite blocks for v16 and the first v19 pass; XOR on later v19 passes.
- Use the generic block functions on every platform and allow uint32 lanes.

The version distinction is specified by the
[official Argon2 reference implementation](https://github.com/P-H-C/phc-winner-argon2/blob/master/src/ref.c#L164-L174).
No C source was copied. Block mixing, indexing and Blake2b kernels remain the Go
upstream implementation. The caller validates parameters and resource limits
before invocation. This adapter verifies imported hashes; new password hashes
use Django's preferred PBKDF2-SHA256 format.

`TESTS/adapters/django/credentials.py` tests Argon2 i/id/d for v16, implicit v16 and
v19 against `argon2-cffi` through Django's actual password verifier, including
wrong-password denials. This is an Assozeta compatibility extension, not a claim
about Kuayle password formats.
