# Login image primitives — not a complete image processor

This package implements prerequisites of Django's `compress_base64`:
permissive data-URI decoding, size-policy decisions and decoded 8-bit pixel
transformation, plus a bounded libjpeg JPEG encoder. It does **not** implement
`domain.LoginImageProcessor`, recognize all Pillow file formats or decode file pixels. Public login remains
unwired; no unsupported image is silently accepted as completed parity.

`DecodeDataURI` splits at the first comma and treats the header as opaque. It
matches Python's non-strict Base64 handling, including ignored ASCII garbage,
leading/excess padding and termination at complete padding. Any non-ASCII payload
is rejected, even after terminating padding. Incomplete groups return no partial
bytes. Callers must distinguish malformed data (Django returns the original URI)
from cancellation or resource-budget failures.

`Decide` preserves the actual strict 1 MiB **decoded file-byte** threshold:

- When the computed 200-pixel bounding size already matches the source and data
  is at most 1 MiB, Django returns the original URI without pixel loading.
- Otherwise, at or below 1 MiB it re-encodes at the original dimensions.
- Above 1 MiB it resizes to the computed bounding dimensions, including enlargement
  of small images with large encoded data.
- Floating-point truncation is preserved: 289×100 becomes 199×69. An extremely thin
  image can produce a zero dimension; Django catches that resize failure and
  returns the original. `Decide` reports `ErrDimensions` for that condition.

`Transform` receives a normalized decoded 8-bit raster. It supports L, RGB, RGBA,
RGBX, CMYK and YCbCr channels. File codecs must normalize one-bit L data and other
supported input representations correctly before calling it. RGBA is pasted onto
white **before** resampling, with the same integer rounding as Pillow. Other modes
retain their channels; the encoder applies their mode-specific packing.

The separable Lanczos implementation is derived from the pinned
[Pillow 11.1.0 Resample.c](https://github.com/python-pillow/Pillow/blob/11.1.0/src/libImaging/Resample.c):
three-lobe support, float32 box endpoints, normalized 22-bit fixed-point weights,
horizontal then vertical passes and 8-bit clipping after each pass. The original
[MIT-CMU license](LICENSE.pillow) and copyright notices are retained, including in
the Go image's supporting license files. These pixel stages are pure Go. The JPEG
encoder described below uses a native helper, without Python or Django.

Inputs remain unchanged and returned pixel storage does not alias them. The caller
supplies a mandatory logical working-byte limit. Input pixels, output/intermediate
buffers, alpha conversion and coefficient storage are checked before allocations;
coefficient arithmetic cannot wrap the budget. The limit does not claim to bound
allocator overhead or process RSS. Global admission, encoded-file/decoder budgets
and supported-data sizing remain part of the full processor work. Cancellation is
checked during ASCII validation, Base64 decoding, alpha conversion, coefficient
calculation and pixel passes; no failure returns a partial buffer.

## Evidence and remaining work

`TESTS/adapters/django/login_image_pixels.py` runs through the private account
adapter. Ten explicitly expected decisions call the actual Django helper with PNG
inputs padded to exact file sizes. Fifty-four decoded pixel vectors cover six
modes, identity, shrink, enlarge and single-axis transformations. Explicit white
alpha/constant/identity assertions anchor the oracle; Go output must equal every
Pillow output byte with **no tolerance**. Another 19,535 short Base64 vectors
compare against CPython, alongside explicit known padding/Unicode cases. Every
managed database table remains unchanged.

Go tests also exercise logical memory limits, overflow prevention, cancellation
at multiple processing stages and 32 concurrent transformations sharing immutable
source pixels. There are no image snapshots, real customer images or generated
fixture payloads committed to the repository.

## JPEG encoding and process boundary

`NewJPEGEncoder` requires an absolute, trusted executable path and explicit
positive input/output byte limits, process concurrency and timeout. A shared
instance admits a bounded number of child processes; queue time counts toward the
timeout. `Encode` validates layout/mode/dimensions before spawning and supplies
packed pixels on stdin without a shell. The child receives only `LC_ALL=C`, not
application secrets. Cancellation and output overflow kill and reap the process;
all failures return no bytes and release admission. Error messages contain no
image payload, credentials or child stderr.

The bundled `native/jpeg_encode.c` accepts L/RGB/RGBX/YCbCr/CMYK, inverts CMYK
samples as [Pillow's JPEG encoder](https://github.com/python-pillow/Pillow/blob/11.1.0/src/libImaging/JpegEncode.c)
does and uses libjpeg's quality-75 defaults,
standard sampling/markers and non-progressive encoding. RGBA must first pass
through `Transform`. Dimensions are limited to libjpeg's 65,500 pixels per axis.
The helper reads one row at a time, verifies exact input length and buffers output
under its own byte ceiling before writing stdout. Go separately bounds stdout and
cancels an overflowing or stalled process. These are logical payload/process
limits, not a total RSS cap; libjpeg working memory and Go allocator overhead are
additional. Whole-pipeline decoder/transform admission remains required.

`BE_GO/Dockerfile` compiles the helper against Alpine 3.21's static libjpeg-turbo
in a separate build stage. The server remains `CGO_ENABLED=0`; the final image
contains `/app/image-codec`, its library version and license notices, runs as UID
10001 and needs no compiler or Python. The helper is not yet wired into login.
This software is based in part on the work of the Independent JPEG Group.

`TESTS/adapters/django/login_jpeg.py` establishes 180 exact JPEG vectors for five
modes, four deterministic patterns and nine sizes around sampling/block boundaries.
Explicit dimensions, color modes, quality tables, sampling, density and metadata
assertions anchor the byte oracle. All 54 preceding pixel transformations also
compare their encoded JPEG bytes against Pillow. Tests cover exact output limits,
truncated/extra stdin, unsupported modes, overflow, disappearing executables,
non-inherited environment, running/queued cancellation, timeout, process failure
and 32 concurrent encodes. No partial failed output is returned.

The local oracle passes with Pillow 11.1.0/libjpeg-turbo 3.1.0 against system
libjpeg-turbo 3.2.0. `TESTS/adapters/go/login_jpeg_image.py` runs the same 180-vector
encoder test and native failure protocol inside the actual read-only, networkless
image; the inspected Alpine library is 3.0.4 and output also matches exactly.
CI repeats this check on the image it builds. These are tested vectors and library
versions, not a blanket claim about every codec version or architecture.

Still required: full file-format recognition and lazy-header behavior, decoding
(including animation/frame, metadata and color-mode rules), invalid/truncated-file
fallback behavior, global resource admission, supported-data sizing and real
response/HTTP composition. These checks do not certify complete image parity.
