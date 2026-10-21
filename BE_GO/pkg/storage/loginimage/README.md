# Login image primitives — not a complete image processor

This package implements prerequisites of Django's `compress_base64`:
permissive data-URI decoding, size-policy decisions and decoded 8-bit pixel
transformation, bounded libjpeg JPEG encoding/decoding, and PNG header/pixel
and chunk-stream decoding. It does
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
RGBX, CMYK and YCbCr channels, plus mode `1` represented as unpacked 0/255
bytes. File codecs must normalize supported input representations correctly. RGBA is pasted onto
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
and static zlib in a separate build stage. The server remains `CGO_ENABLED=0`; the final image
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

## PNG compressed pixels

`DecodePNGScanlines` accepts the compressed bytes of one PNG tile plus an admitted
pixel specification. It implements all five PNG row filters, Adam7's seven passes
and all 15 depth/color combinations accepted by Pillow 11.1. This is a pixel
primitive: it does not recognize PNG files, parse/check chunk headers or CRCs,
interpret metadata, select APNG frames or implement the complete PNG file policy.

Grayscale 2/4-bit samples expand to L; one-bit samples retain mode `1` as unpacked
0/255 bytes. Palette indices retain P, 8-bit gray-alpha retains LA, and grayscale
16-bit retains I;16 with little-endian samples. RGB/RGBA 16-bit samples use their
high bytes. Pillow's 16-bit gray-alpha case expands to RGBA, unlike 8-bit LA.
These distinctions matter: P/LA/I;16 cannot be saved as JPEG. Mode `1` uses the
nearest-neighbour path described below, retaining its binary samples.

Input, output and two maximum-width row buffers count toward an explicit logical
working-byte budget checked before allocation. Zlib internals and allocator/RSS
overhead are not included. Invalid specifications and overflowing dimensions
return no raster. Cancellation is checked while reading compressed data, between
rows and during filter/unpack loops. Inputs are immutable and concurrent calls
share no decoder state. Missing zlib trailers after all required scanlines are
tolerated; a checksum failure reached while producing a required row fails.
Surplus inflated bytes are ignored, so a corrupt checksum beyond those bytes
must not reject an otherwise accepted image. A valid zlib stream end after a
complete row is also accepted, leaving unvisited pixels zero, including in Adam7.
An incomplete row or missing trailer before the remaining rows still fails.
These rules apply to a single compressed stream: blindly concatenating file IDAT
chunks changes Pillow's checksum/error timing and is not a complete file reader.

`TESTS/adapters/django/login_png_pixels.py` establishes 803 explicit cases:
750 sample/filter/interlace combinations across five sizes, including buffers
larger than the deflate window, plus 53 truncation/filter/checksum/early-end cases. Expected
pixels are constructed directly from samples and independently compared with
Pillow; outputs are not merely copied from the reference decoder. The account
adapter also calls the actual Django helper for all cases. At unchanged sizes,
480 produce exact JPEG bytes from decoded pixels/white alpha composition, while
300 unsupported modes and 23 invalid pixel streams preserve the original URI.
Mode `1` passes through the production transform and encoder. The Go test
checks the separate transform/encoder composition without claiming PNG file
admission. Limits at the exact boundary, integer overflow, mid-decode cancellation
and 32 concurrent Adam7 decodes are covered. The non-root/read-only/networkless
image runs the same PNG pixel and JPEG-composition oracle.

## Lazy PNG header and metadata admission

`ReadPNGHeader` reads through the first recognized IDAT/fdAT header, or IEND,
without requiring compressed pixels. It reports canvas mode/depth, interlacing,
consumed bytes, data offset/length, first-tile rectangle, declared frame count,
default-image selection, pixel warnings and the inherited UTF-8 comment. Returned
metadata does not alias input. A mandatory consumed-byte budget limits parsing;
trailing compressed data does not count toward that header budget. Errors and
cancellation return no partial header.

The parser preserves Pillow's chunk-identifier rules, pre-pixel CRC checks,
repeated IHDR behavior, accepted depth/color combinations, ignored compression
byte, sticky interlace flag, metadata payload checks and hard pixel limit. Pixel
CRC and subsequent metadata are deliberately deferred, as in lazy `Image.open`.
IEND may end an admitted header with no tile. APNG handling validates declarations,
frame sequences and rectangle bounds, preserves default-image/frame-count rules
and distinguishes invalid text-provided rectangles from rejected headers. Early
IDAT/fdAT chunks without a pixel mode preserve the observed unknown-handler
consumption and default-image state. This reports the first frame's admission; it
does not decode/composite APNG frames or expose a general metadata dictionary.

Text/ICC handling preserves malformed-stream fallbacks, incomplete zlib streams,
Latin-1/UTF-8 interpretation, the 1 MiB inflated-chunk ceiling and 64 MiB cumulative
text-character ceiling. Repeated keys still count toward the cumulative ceiling.
Only values that influence this image path are retained. Comments become UTF-8;
an empty text comment is absent in the resulting JPEG, unlike an empty JPEG COM.
Text keys that affect interlacing, rectangles, disposal or default-image selection
retain their observable behavior. Inflation limits and consumed input bound
logical payloads; allocator/zlib overhead and total RSS remain separate.

`login_png_header.py` specifies 394 explicit Pillow outcomes, including malformed
chunks and CRCs, all supported modes, pixel-limit boundaries, truncated metadata,
compressed-text limits, duplicate headers, APNG declarations/sequences/rectangles,
metadata collisions and early data chunks. The actual Django helper verifies all 235
valid 200×100 fixture headers return the original URI with **zero pixel loads**,
observed through a delegating loader. Go checks exact header budgets, immutable
metadata, cancellation during text inflation/conversion, Unicode character
accounting and 32 concurrent readers. The non-root/read-only/networkless image
runs the same header oracle alongside the existing JPEG and PNG pixel checks.

Still open for complete PNG files: post-pixel metadata and error behavior,
first-frame canvas composition, whole-file admission/limits and response wiring.
The separate stream decoder below now preserves pixel-chunk boundaries.


## One-bit nearest-neighbour transforms

Pillow forces nearest-neighbour sampling for mode `1` even when its caller asks
for Lanczos. `Transform` preserves this behavior, including float32 source-box
endpoints and the accumulated double coordinates in
[Pillow 11.1 Geometry.c](https://github.com/python-pillow/Pillow/blob/11.1.0/src/libImaging/Geometry.c).
For example, resizing two pixels `[black, white]` to seven keeps the fourth pixel
black; recomputing each coordinate with multiplication changes that boundary.
The retained Pillow license covers this derived implementation as well.

Binary rasters require every byte to be 0 or 255. Both transformation and JPEG
encoding reject other sample values before producing output. Identity transforms
clone pixels without allocating an index table; resizes reserve input, output and
one integer index per target column against the logical byte budget. Cancellation
is checked during validation, coordinate construction and pixel copying. The
encoder supplies validated binary samples as L to the existing native helper.

`login_bilevel.py` checks 210 exact nearest/JPEG vectors, including horizontal and
vertical boundary scans, constant patterns, identity, shrink and enlargement.
Another 18 ordinary one-IDAT PNG cases check the actual Django helper at small,
exactly 1 MiB and 1 MiB + 1 file sizes, including zero target dimensions. Go composes
header admission, size decision, scanline decoding, transformation and encoding
for these fixtures. Exact budgets, invalid samples, ownership, cancellation and
32 concurrent transformations/encodes also pass. The shipped non-root, read-only,
networkless image runs the same oracle. These tests do not establish complete PNG
chunk/post-pixel/APNG handling or public login wiring.


## PNG chunk-stream decoding

`PNGStreamDecoder` accepts an admitted tile, its first data-payload offset/length
and the last APNG sequence number. The bundled native helper uses static zlib,
with the 64 KiB per-chunk reads of
[Pillow 11.1 PngImagePlugin.load_read](https://github.com/python-pillow/Pillow/blob/11.1.0/src/PIL/PngImagePlugin.py)
and the row-limited inflate calls of
[ZipDecode.c](https://github.com/python-pillow/Pillow/blob/11.1.0/src/libImaging/ZipDecode.c).
Go applies the same filter/sample unpacker used by `DecodePNGScanlines`. This
preserves all fifteen modes and Adam7 without duplicating sample conversion in C.

Chunk boundaries affect acceptance. For the explicit 3×2 L fixture, a corrupt
checksum in one IDAT is rejected; splitting after byte 11 lets Pillow finish its
pixels before the second IDAT, so that checksum is never decompressed. The reader
also preserves empty chunks, DDAT continuation, fdAT length/sequence checks and
requested-versus-actual byte accounting when the active chunk is truncated.
Pixel CRCs are skipped, as in Pillow. It returns the file position, remaining
declared chunk bytes and sequence at the last pixel read so later metadata parsing
can continue from the correct state. A later truncated IDAT can still fail in
`load_end`; successful pixel decoding alone does not accept the complete file.

Construction requires positive file/working-byte limits, child concurrency and
a timeout. One deadline covers queueing, inflation and Go unpacking. Before
spawning, Go checks input, maximum filtered-row payload/frame, normalized pixels
and two full-width row buffers against the logical working budget. The child
separately bounds its file and filtered-row allocations and emits a framed result
only after decoding completes. These checks do not cap aggregate process RSS,
allocator growth, zlib internals or the simultaneous copies across the pipe.
The existing process boundary supplies environment isolation, bounded stdout,
child killing/reaping and admission reuse. Every failure returns no pixels.

`login_png_stream.py` specifies 852 cases: the existing 803 independently
constructed pixel/filter cases plus 49 chunk/read-boundary cases. A delegating
Pillow observer checks explicitly calculated file positions, remainders and APNG
sequences for successful decodes; invalid pixel streams and a post-pixel truncation
are distinguished. Tests cover exact byte budgets, malformed native arguments and
frames, invalid filters, overflow, disappearing executables, running/queued
cancellation, timeout, mid-unpack cancellation and 32 concurrent Adam7 decodes.
The shipped non-root/read-only/networkless image runs the pixel and native-protocol
oracles and verifies the delivered zlib license and package version.

`TESTS/adapters/go/login_png_sanitizer.py` also runs all 852 cases under
AddressSanitizer/UndefinedBehaviorSanitizer: successful frames must exactly equal
the separately verified ordinary helper, invalid streams must exit 2 with no
output, and diagnostics fail the check. Eighteen additional malformed-protocol
and budget cases check exact failure exits under both builds. Thus a sanitizer
abort cannot be hidden
by Go's generic decoding-error mapping. CI runs this check alongside the full
account adapter and image oracle. This component is not yet the complete PNG
file processor or public login image service.
