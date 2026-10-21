# Login image primitives — not a complete image processor

This package implements prerequisites of Django's `compress_base64`:
permissive data-URI decoding, size-policy decisions and decoded 8-bit pixel
transformation, plus bounded libjpeg JPEG encoding and pixel decoding. It does
**not** implement `domain.LoginImageProcessor`, recognize all Pillow file formats
or wire the public service. JPEG marker/EXIF/MPO admission and the complete
JPEG file pipeline are available as separate components.
Public login remains unwired; no unsupported image is silently accepted as completed parity.

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
codec described below uses a native helper, without Python or Django.

Inputs remain unchanged and returned pixel storage does not alias them. The caller
supplies a mandatory logical working-byte limit. Input pixels, output/intermediate
buffers, alpha conversion and coefficient storage are checked before allocations;
coefficient arithmetic cannot wrap the budget. The limit does not claim to bound
allocator overhead or process RSS. JPEG pipeline admission is bounded; budgets for other file
decoders and supported-data sizing remain part of the full processor work. Cancellation is
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
additional. `JPEGFileProcessor` supplies admission across the JPEG stages.

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

## Lazy JPEG marker headers

`ReadJPEGMarkerHeader` reads only through the first SOS segment and reports the
consumed header bytes, dimensions, mode and last comment. It accepts recognizable
header-only files even when pixel tables or entropy data are absent. This matters
because Django returns an original URI at the exact 200-pixel bounding size before
loading pixels. It does not consume trailing entropy data or apply the 1 MiB file
policy; callers must still use the complete decoded file length for `Decide`.

The parser follows [Pillow 11.1's marker handlers](https://github.com/python-pillow/Pillow/blob/11.1.0/src/PIL/JpegImagePlugin.py),
including fill/escaped markers, ignored junk, repeated frames, component-mode
selection, quantization payload lengths, JFIF/Adobe truncation, pending ICC checks,
Photoshop resource truncation and non-positive segment payload lengths. Empty
comments differ from absent comments. It rejects zero final dimensions and the
pinned Pillow hard pixel limit, and reports the intermediate pixel-count warning.
A mandatory consumed-header byte budget and cancellation checks bound parsing;
errors return no partial header. Returned metadata does not alias input bytes.

EXIF fragments are combined, and the last MPF payload/offset and Ultra HDR marker
presence are retained for `InspectJPEG`, which applies EXIF DPI and MPF
first-frame admission. The marker reader alone does not perform that admission.
Neither component is wired into the public login service.

`TESTS/adapters/django/login_jpeg_header.py` specifies 1,352 explicit outcomes:
all 256 marker codes, empty/incomplete payloads, component/precision/quantization
cases, metadata truncation, comments, size limits, 180 ordinary JPEG headers and
every prefix of a selected JPEG across its lazy SOS boundary. Pillow supplies
actual recognition evidence; expected validity, dimensions, mode, comment,
warning and stop position are asserted independently. The account adapter also
checks three real Django header-only passthrough cases whose pixels cannot load.
A delegating loader observer confirms zero load attempts, distinguishing early
passthrough from a caught-error fallback with the same URI output. Go verifies exact header budgets, metadata ownership/ordering/offsets, cancellation
during scanning and 32 concurrent readers. The same header oracle runs in the
non-root, read-only, networkless image rehearsal alongside the existing codecs.
These marker cases do not certify EXIF/MPO processing or other file formats;
the separate metadata suite covers the former.

## JPEG decoding and inherited comments

`NewJPEGDecoder` uses the same bounded process runner with independent admission
from the encoder. The encoded file is bounded before spawning. The native helper
reads exactly the declared input, validates 8-bit precision and L/RGB/CMYK output,
checks decoded dimensions/byte count before allocating pixels and emits one
complete binary frame after decoding. Go validates the frame length, mode,
dimensions and comment length; no failure returns partial pixels. Raster bytes are
bounded separately from a fixed maximum of 65,533 comment bytes and a 20-byte
frame header. These payload bounds do not include libjpeg's internal allocations
or allocator overhead. `JPEGFileProcessor` bounds admission across decoding,
transformation and encoding for its shared instance.

The decoder follows [Pillow's ordinary JPEG settings](https://github.com/python-pillow/Pillow/blob/11.1.0/src/libImaging/JpegDecode.c),
including fancy upsampling, the normal DCT and inverted CMYK packing. It suspends
on exhausted input instead of synthesizing an EOI marker. Fatal errors and missing
scanlines fail; finish suspension after all rows is tolerated as in Pillow. EXIF
orientation and ICC metadata do not transform the pixels. This is pixel decoding,
not a replacement for Pillow's Python header parser or lazy `Image.open` behavior.

`JPEGImage.Comment` preserves the last COM marker before the first scan, including
empty/binary comments. Later COM markers do not replace it. The caller carries
this metadata across transformations and uses `EncodeWithComment`; ordinary
`Encode` emits no inherited comment. The latter preserves Pillow's initial output
buffer constraint: for narrow images, comments of 65,512 bytes fail for L/RGB and
65,514 fail for CMYK, while the preceding byte count succeeds. Wider images enlarge
the buffer. These save failures must remain original-URI fallbacks in the eventual
image processor, not silently successful conversions.

`TESTS/adapters/django/login_jpeg_decode.py` establishes 312 exact decoded-pixel and
comment cases, including sampling/quality, progressive/optimized coding, RGB
components, CMYK/YCCK, restart markers, metadata, tolerated damaged streams,
concatenated files and comment boundaries. Of these, 310 also produce identical
transformed/re-encoded JPEG bytes and two preserve Pillow's save failure. Another
1,556 explicitly invalid streams include every incomplete prefix of selected
baseline/progressive files. The full account adapter calls Django's actual
`compress_base64` for all 1,868 output/fallback cases. Standalone execution tests
the codec stages without claiming that Django application check.

Tests also cover native input framing, exact raster budgets, malformed output
frames, comment lengths, process failures, environment isolation, running/queued
cancellation, timeout and 32 concurrent decodes. The image adapter runs the same
1,868 decode cases and both native protocol suites alongside the 180 encoder
vectors using the shipped non-root codec. The native decoder is additionally
checked with AddressSanitizer/UndefinedBehaviorSanitizer using exact exit codes
and complete frame comparisons, including invalid input; generic error mapping
cannot hide sanitizer failures.

## JPEG metadata and complete file pipeline

`InspectJPEG` interprets EXIF DPI only when a JFIF DPI has not already been set.
It preserves Pillow's malformed-directory fallback and uncaught scalar-index
failure. MPF supports the observed TIFF byte orders/types, partial directories,
first-value count semantics and first-frame MPO adoption. Unreadable MPF normally
retains the base JPEG; truncated MP records reject it. Unsupported MP formats
fall back before later records are read. Ultra HDR suppresses MPO adoption but
does not bypass malformed-record validation. Subsequent MPO frame seeking is not
needed by `compress_base64` and is not implemented as a general image API.

`NewJPEGFileProcessor` requires positive header, raster, encoded-file/output and
transform limits, concurrency and timeout. `Process` consumes decoded file bytes;
other signatures return `ErrNotJPEG` for the future format dispatcher. One shared
admission slot covers header inspection, policy, native decode, transform and
native encode; queue time counts toward one overall deadline. Native children
retain their own bounded process runner. Invalid JPEGs and expected Pillow save
failures return `KeepOriginal`; cancellation, limits and unavailable executables
return errors without output. Exact-bound passthrough never invokes the codec.
The authoritative inherited comment comes from the lazy header, including a
comment before an EOI/SOI tables-only boundary. No orientation/ICC conversion is
introduced.

`login_jpeg_metadata.py` establishes 226 explicit EXIF/MPF admission outcomes and
exact first-frame JPEGs. Tests cover cancellation inside directories/MP records
and 32 concurrent MPO inspections. `login_jpeg_file.py` adds 73 explicit policy
cases: three channel modes, exact 1 MiB boundaries, shrink/enlarge, floating-point
truncation, zero-sized targets, header-only passthrough/failure and tables-only
comments. The complete file processor also reruns the 226 metadata and 1,868
decoder cases, returning `ErrNotJPEG` for non-JPEG signatures. Actual Django helper
calls verify the original URI versus exact encoded output. Tests exercise input,
header, raster and transform budgets, 32 concurrent resizes, and cancellation of
an encoder while another whole-pipeline request waits. Cancellation reaps the
child, releases all stages and permits a subsequent request. The shipped
non-root, read-only, networkless image runs the same file and metadata oracles.

Still required: other format decoders (including animation/frame, metadata and
color-mode rules), their invalid/truncated-file fallback policy, global admission
across formats, supported-data sizing and real response/HTTP composition. These
checks do not certify complete image parity.
