# Default storage trust

`cacert.pem` is copied without modification from certifi 2026.10.19, the exact
version pinned in `BE/requirements.txt`. The upstream `LICENSE` is retained.
`UPSTREAM.json` records both file hashes and the certificate count. These are
public CA certificates, not private fixture certificates or application keys.

Go embeds this bundle only for the owned storage and credential transports.
An absent AWS/shared-profile/requests CA selection uses this pool, matching
botocore's default certifi source. It does not add system roots or roots selected
by `SSL_CERT_FILE` or `SSL_CERT_DIR`. Nonempty explicit bundles replace this pool;
the separately characterized explicit-empty legacy setting disables verification.

Update the Python pin, both upstream files and their manifest together. Run
`python TESTS/check_storage_ca_bundle.py` in the pinned production Python
environment, then repeat storage transport, Django oracle and packaged Linux
acceptance. The check requires byte-for-byte equality with the installed Python
distribution and rejects undeclared files. Do not update only one backend or
infer trust equivalence across different certifi releases or OS certificate sets.
