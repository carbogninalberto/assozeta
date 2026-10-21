# Login image pixel core — not a complete image processor

This package implements three prerequisites of Django's `compress_base64`:
permissive data-URI decoding, size-policy decisions and decoded 8-bit pixel
transformation. It does **not** implement `domain.LoginImageProcessor`, recognize
all Pillow file formats, decode file pixels or encode JPEG. Public login remains
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
retain their channels; a future encoder must apply its mode-specific packing.

The separable Lanczos implementation is derived from the pinned
[Pillow 11.1.0 Resample.c](https://github.com/python-pillow/Pillow/blob/11.1.0/src/libImaging/Resample.c):
three-lobe support, float32 box endpoints, normalized 22-bit fixed-point weights,
horizontal then vertical passes and 8-bit clipping after each pass. The original
[MIT-CMU license](LICENSE.pillow) and copyright notices are retained, including in
the Go image's supporting license files. This is pure Go; no Python or native
codec runtime was introduced by this component.

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

Still required: full file-format recognition and lazy-header behavior, decoding
(including animation/frame, metadata and color-mode rules), byte-compatible JPEG
encoding, invalid/truncated-file fallback behavior, global resource admission,
container codec delivery and real response/HTTP composition. The inspected local
Pillow 11.1.0 uses libjpeg-turbo 3.1.0; system libjpeg-turbo is a different version.
Codec equivalence must therefore be demonstrated, not inferred from library names
or visual similarity. These pixel checks do not certify complete image parity.
