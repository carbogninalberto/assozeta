# Offline manual availability

`Loader.Available` implements the existing Django navigation flag. It performs no database queries, writes or network fetches. This flag does not promise that a particular reader section, image or assistant answer is accessible.

The precedence is the installed immutable bundle, a nonblank configured publisher URL, development preview, development installation, then a revision/release-bound runtime index. Invalid evidence encountered at an earlier layer suppresses later fallback, matching Django. Development alternatives require development mode and no bound revision or manual run ID. A configured publisher reports availability without fetching it or exposing its URL.

The installed bundle requires the original sealed index, raw index hash, receipt identity, application revision, release and manual provenance. Availability intentionally does not verify image bytes or filter private/unverified chunks: those are separate reader and release checks in Django. The existing release publisher must still run its asset validation before delivering a package. No manual reader, asset handler, remote synchronization or assistant implementation is included here.

`Config.Load` takes image-owned `CODE_REVISION`, `MANUAL_REQUIRED` and `release-manual` from the executable directory. An existing revision file, including an empty or `unknown` one, overrides the environment fallback. Explicit index/development paths retain their working-directory meaning. Default paths move with the executable. No new credential namespace is introduced; AI and Stripe flags preserve Django string truthiness, including whitespace.

The Go release image must receive exactly the same immutable package as Django, including the required marker and matching application revision/version. The separate delivery increment wires these inputs into the Go Dockerfile and release workflow; actual image execution remains a required integration check. Delivery acceptance requires both the existing full Django bundle/asset gate and the explicit Go bundle check with no fallback. Merely compiling this loader cannot satisfy that gate.

Seals reproduce Python compact sorted Unicode JSON, including arbitrary-precision integer and floating-point spelling. Unpaired surrogate escapes cannot authenticate a replacement character. The two-entry cache uses resolved path, device/inode, size, modification and change times, and checks file stability across a load. Receipt and pointer files remain live. Source hashes and screenshot bytes are not re-read for this flag. Index loading costs scale with package size; this implementation makes no constant-memory or bounded-artifact-size claim.

Run the required offline Django/Go differential with the repository's production Python:

```
python TESTS/adapters/django/manual_availability.py
```

It checks 47 explicit cases without skips, retains actual Django observations and Go race results, and removes its private artifact directory. The fixture uses synthetic sealed files, no customer corpus and no provider. Unit race tests also cover inode replacement with unchanged size/mtime, concurrent readers, deletion, cancellation, symlink ownership and Unicode seals. Configuration tests cover marker precedence, explicit paths, environment truthiness and redaction.

## Explicit bundle gate

`manual.CheckBundle` and `cmd/check-manual` validate the bundle independently of navigation availability. Their CLI matches the existing Django build gate:

```
check-manual --root /app/release-manual --revision REVISION --release VERSION --required true
```

An explicitly empty root denotes the working directory, matching pathlib. Optional missing or placeholder packages pass; optional present-invalid packages fail. Required mode cannot be satisfied by a configured publisher or another index. Every declared screenshot is checked, including screenshots in private chunks. Checks reproduce Django's PNG suffix, owned path, content hash and minimal PNG/IHDR header rules; neither gate is a full image decoder. Errors omit artifact paths, metadata and credentials.

`python TESTS/adapters/django/manual_bundle.py` compares 19 explicit cases against the actual Django bundle CLI and the newly built Go command. It uses synthetic packages and private temporary paths, asserts exact case coverage and command redaction, and removes the command and package fixtures before marking success. The Dockerfile now builds and invokes this command as the nonroot runtime user; the publish workflow downloads the same verified artifact into both backend contexts. Actual image execution is a separate acceptance check.

## Packaged checker proof

The optional image adapter consumes an already built local image; it does not build, pull or retag one:

```
python TESTS/adapters/go/manual_image.py --image IMAGE \
  --expected-version VERSION --expected-revision REVISION --expected-required false
```

It resolves the immutable image ID, checks its configured UID10001/default server/HTTP health contract, reads image-owned binary and metadata hashes, verifies bundle ownership and modes, and runs the actual packaged checker. The same 19 independently authored fixtures are first measured against Django and then mounted as data only. Checker containers have a read-only root and mount, no network, no capabilities and no inherited HTTP health check. Private containers and fixtures must be removed before success is recorded.

Local source hashes and image binary/artifact hashes are retained independently. An optional `--build-receipt` records the hash of root's independently retained build evidence; this adapter does not manufacture a source-to-image attestation. A synthetic fixture pass or optional placeholder image is not proof of a genuine required release package. The release pipeline still needs an actual newly verified artifact and its image validation.
