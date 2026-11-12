This directory retains the external module `github.com/aws/aws-sdk-go-v2/config`
at v1.33.9, including its Apache-2.0 license, tests and test data. It is not a
second Assozeta application module. The root module replaces only this external
dependency; its normal dependency versions remain pinned by the root go.mod.

The declared patch adds one default-off option to delegate CA configuration to
the supplied HTTP client. Storage credentials use it because Django loads CA
files at HTTPS connection time, whereas the upstream resolver eagerly loads them
and rejects the existing bounded HTTP client. All other resolvers and credential
selection code remain unchanged. The option does not itself disable TLS checks.

`UPSTREAM.json` binds the original module sum and every original file.
`owned-http-trust.patch` is the complete source delta. Run from the repository:

```
python3 TESTS/check_aws_config_patch.py
cd BE_GO
go test github.com/aws/aws-sdk-go-v2/config/...
```

Run upstream tests in the application module context: upstream's original
go.mod includes relative development replacements which intentionally remain
unmodified. An upgrade must refresh the upstream module, hashes, exact patch,
root pin and drift guard together, then repeat upstream and storage regressions.
