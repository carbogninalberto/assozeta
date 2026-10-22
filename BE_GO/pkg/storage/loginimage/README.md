# Login image primitives — not a complete image processor

This package implements bounded Pillow-compatible file pipelines and the shared
`Processor`, which satisfies `domain.LoginImageProcessor`. The processor composes
eighteen existing pipelines, including the BMP/DIB, PCX/DCX and JPEG/MPO paths,
with data-URI decoding and JPEG output. Its registry is **incomplete**: missing
codecs and unresolved ambiguous headers return `ErrImageDecoderUnavailable`.
That error is an implementation gap, not an intentional compatibility correction.
The public-login root now composes this processor. Missing formats remain
explicit errors; no unsupported image is silently accepted as parity.

The current production reference is Python 3.13.16 / Pillow 13.0.0. The full
accounts adapter and rebuilt non-root shipped-image oracle pass the implemented
pipelines, including 84 pixel transformations, 19,535 Base64 comparisons and 94
shared URI cases. The new AVIF registry entry remains an explicit unavailable-
decoder gap alongside TIFF and other unresolved formats. Earlier source links
identify the original implementation provenance; current corrections are noted
below. Evidence: `quality-reports/go-migration/security-baseline/`.

`DecodeDataURI` splits at the first comma and treats the header as opaque. It
matches Python's non-strict Base64 handling, including ignored ASCII garbage,
leading/excess padding. CPython 3.13.16 ignores padding inside the data and
validates the final group at EOF; bytes after an apparent padded group still
participate in decoding. Any non-ASCII payload is rejected. Incomplete groups return no partial
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
8-bit clipping after each pass. Pillow 13
[selects vertical first](https://github.com/python-pillow/Pillow/blob/13.0.0/src/libImaging/Resample.c)
when the height reduction exceeds twice the width reduction; Go now follows that
order and budgets the corresponding intermediate raster before allocating. The original
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
inputs padded to exact file sizes. Eighty-four decoded pixel vectors cover six
modes, identity, shrink, enlarge, single-axis transformations and both sides of
the strict pass-order boundary. Independently ordered single-axis transforms
check the expected pass order. The shipped-image oracle includes this matrix
and the Base64 comparisons as well as the file codecs. Explicit white
alpha/constant/identity assertions anchor the oracle; Go output must equal every
Pillow output byte with **no tolerance**. Another 19,535 short Base64 vectors
compare against CPython, alongside explicit known padding/Unicode cases. The
current padding boundary follows [CPython 3.13.16](https://github.com/python/cpython/blob/v3.13.16/Modules/binascii.c). Every
managed database table remains unchanged.

Go tests also exercise logical memory limits, overflow prevention, cancellation
at multiple processing stages and 32 concurrent transformations sharing immutable
source pixels. Cancellation probes cover both pass orders through their final
reachable checkpoint; a fixed late trigger from the old pass order no longer
necessarily fires after an earlier reduction shortens the work. There are no image snapshots, real customer images or generated
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
assertions anchor the byte oracle. All 84 preceding pixel transformations also
compare their encoded JPEG bytes against Pillow. Tests cover exact output limits,
truncated/extra stdin, unsupported modes, overflow, disappearing executables,
non-inherited environment, running/queued cancellation, timeout, process failure
and 32 concurrent encodes. No partial failed output is returned.

The current local oracle passes with Pillow 13.0.0/libjpeg-turbo 3.2.0 against
system libjpeg-turbo 3.2.0. `TESTS/adapters/go/login_jpeg_image.py` runs the same 180-vector
encoder test and native failure protocol inside the actual read-only, networkless
image; the inspected Alpine library is 3.0.4 and output also matches exactly.
CI repeats this check on the image it builds. These are tested vectors and library
versions, not a blanket claim about every codec version or architecture.

## Lazy JPEG marker headers

`ReadJPEGMarkerHeader` reads through the first SOS segment or EOF between markers and reports the
consumed header bytes, dimensions, mode and last comment. It accepts recognizable
header-only files even when pixel tables or entropy data are absent. This matters
because Django returns an original URI at the exact 200-pixel bounding size before
loading pixels. It does not consume trailing entropy data or apply the 1 MiB file
policy; callers must still use the complete decoded file length for `Decide`.

The parser follows [Pillow 13's marker handlers](https://github.com/python-pillow/Pillow/blob/13.0.0/src/PIL/JpegImagePlugin.py),
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
The public-login root now reaches these components through the shared processor.

`TESTS/adapters/django/login_jpeg_header.py` specifies 1,364 explicit outcomes:
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
`Encode` emits no inherited comment. Pillow 13 accepts comments through the
JPEG marker limit of 65,533 bytes, including narrow L/RGB/CMYK images. Go and the
native helper now allow those comments while rejecting longer markers. The former
Pillow 11 initial-buffer failure boundary is superseded; output-byte limits still
apply before any encoded output is exposed.

`TESTS/adapters/django/login_jpeg_decode.py` establishes 314 exact decoded-pixel,
comment and re-encoded JPEG cases, including maximum-size markers. Another 1,556
explicitly invalid streams include incomplete baseline/progressive prefixes.
The current-reference focused oracle and actual Django helper pass all 1,870
cases. Tests cover sampling/quality, progressive/optimized coding, RGB components,
CMYK/YCCK, restart markers, metadata, tolerated damage and concatenated files.
`login_jpeg_comment_sanitizer.py` adds 330 exact native-process checks with both
ordinary and ASan/UBSan builds: five modes, forty valid comments, output-budget
boundaries, truncated comment/raster input and oversized markers. CI requires it.

Tests also cover native input framing, exact raster budgets, malformed output
frames, comment lengths, process failures, environment isolation, running/queued
cancellation, timeout and 32 concurrent decodes. The image adapter runs the same
1,870 decode cases and both native protocol suites alongside the 180 encoder
vectors using the shipped non-root codec. The native decoder is additionally
checked with AddressSanitizer/UndefinedBehaviorSanitizer using exact exit codes
and complete frame comparisons, including invalid input; generic error mapping
cannot hide sanitizer failures.

## JPEG metadata and complete file pipeline

`InspectJPEG` interprets EXIF DPI only when a JFIF DPI has not already been set.
It preserves Pillow's malformed-directory fallback and uncaught scalar-index
failure. MPF supports the observed TIFF byte orders/types, partial directories,
first-value count semantics and first-frame MPO adoption. The first successfully
read occurrence of each tag wins. Aggregate external tag bytes are limited to
the metadata stream size; oversized tags are skipped while inline tags continue. Unreadable MPF normally
retains the base JPEG, including truncated MP records in Pillow 13. Unsupported MP formats
fall back before later records are read. Ultra HDR suppresses MPO adoption but
malformed records retain JPEG regardless of that flag. Subsequent MPO frame seeking is not
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

`login_jpeg_metadata.py` establishes 236 explicit EXIF/MPF admission outcomes and
exact first-frame JPEGs. Tests cover cancellation inside directories/MP records
and 32 concurrent MPO inspections. `login_jpeg_file.py` adds 97 explicit policy
cases: three channel modes, exact 1 MiB boundaries, shrink/enlarge, floating-point
truncation, zero-sized targets, header-only passthrough/failure and tables-only
comments. The complete file processor also reruns the 236 metadata and 1,870
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
Text keys that affect interlacing, rectangles or default-image selection
retain their observable behavior. Inflation limits and consumed input bound
logical payloads; allocator/zlib overhead and total RSS remain separate.

Pillow 13 requires at least 32 bytes in cHRM and ignores bytes after its first
eight integers. Frame-zero disposal no longer crops invalid text extents during
header admission; pixel loading still rejects those extents.

`login_png_header.py` specifies 398 explicit Pillow outcomes, including malformed
chunks and CRCs, all supported modes, pixel-limit boundaries, truncated metadata,
compressed-text limits, duplicate headers, APNG declarations/sequences/rectangles,
metadata collisions and early data chunks. The actual Django helper verifies all 237
valid 200×100 fixture headers return the original URI with **zero pixel loads**,
observed through a delegating loader. Go checks exact header budgets, immutable
metadata, cancellation during text inflation/conversion, Unicode character
accounting and 32 concurrent readers. The non-root/read-only/networkless image
runs the same header oracle alongside the existing JPEG and PNG pixel checks.

The stream decoder below preserves pixel-chunk boundaries. `PNGFileProcessor`
then composes header admission, pixel decoding, post-pixel validation and
first-frame canvas handling. Public image-service/response wiring remains open.


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
account adapter and image oracle. File-level behavior is covered separately by
`PNGFileProcessor`; the stream component alone does not admit an entire file.


## PNG first-frame file pipeline

`PNGFileProcessor` composes the header parser, native stream reader, tail parser,
canvas placement, resize policy and JPEG encoder under one deadline/admission
limit. It accepts PNG signatures; other formats return `ErrNotPNG`. Malformed
PNG/metadata, unsupported JPEG modes and JPEG-save failures preserve the original
URI decision. Working-limit failures, missing helpers and cancellation propagate
without bytes or a passthrough result. The exact 200-pixel bounding-size fast path
returns before loading pixels, including header-only files.

Header state now survives through the file pipeline, retaining APNG sequence and
cumulative text-memory accounting. `readPNGTail` reproduces `load_end`: the active
chunk remainder may be short, later payloads require exact reads, CRCs are skipped,
short or invalid next headers terminate successfully, and IEND needs no body.
The original animated flag controls stopping before the next fcTL; later acTL
cannot change that decision. Later IHDR updates parser mode/bounds for subsequent
metadata validation while the decoded image retains its original dimensions and
mode. Post-pixel text updates the inherited JPEG comment, with Latin-1/UTF-8 and
existing chunk/cumulative limits. RGBA flattening creates a fresh white RGB image
in Django and drops its comment; Go preserves that distinction.

First-frame tiles are placed on a zero canvas. Blend/disposal do not combine them
with a previous frame because none exists. Pillow 13 rejects all zero-area
tile extents when loading pixels; lazy passthrough remains possible. Its Adam7 decoder also omits
xoff/yoff while placing pixels: an interlaced partial first frame appears at the
origin. Go retains that observed behavior, tested across all 15 mode/depth pairs
with and without acTL, and preserves the original interlace decision if trailing
text changes the metadata flag. These semantics concern the first frame selected
by `compress_base64`, not arbitrary animation playback.

`login_png_file.py` specifies 1,472 exact whole-file JPEG/original decisions,
including the prior pixel/chunk and lazy fixtures, threshold cases, malformed
metadata, cumulative header-plus-tail text limits, comment-save limits, first and
later fdAT sequences, canvas placement and APNG stop rules. Declared samples and
canvas positions anchor the expected JPEG output; both Pillow's complete file
path and the actual Django helper must agree. The non-root/read-only/networkless
image runs the same file oracle alongside all existing image tests. Tests also
cover every stage's logical byte limit, exact tail/canvas budgets, immutable input,
32 concurrent pipelines, mid-tail/canvas cancellation, and running/queued pipeline
cancellation during encoding with child reaping and subsequent admission reuse.

The shared processor below adds partial format dispatch, admission across formats
and concrete response-component checks. Other supported formats and public login
composition remain necessary. No backend parity or release
activation is inferred from the PNG pipeline evidence.

## BMP and standalone DIB file pipeline

`ReadBMPHeader`, `DecodeBMP` and `BMPFileProcessor` cover standalone Windows BMP
and DIB headers (12, 40, 52, 56, 64, 108 and 124 bytes), raw 1/4/8/16/24/32-bit
samples and the exact bitfield layouts accepted by Pillow 13. This follows
`BmpImagePlugin.py` and its raw unpacking behavior; CUR/ICO containers are separate
formats. Palette indices retain mode P, which Django cannot save as JPEG. Gray
palettes instead reduce to mode 1 or L, even when their declared bit depth would
normally use a different packing. Large gray palettes compare wrapped byte
indices; colored palettes with more than 256 complete entries fail when loaded,
while lazy admission can still succeed. The parser retains that distinction.

Raw rows preserve bottom-up/top-down order, optional final padding, 5/6-bit channel
scaling and ignored alpha in ordinary 32-bit BMP. RLE4/RLE8 preserve clipped runs,
EOL padding, absolute runs spanning rows, absolute file-position alignment, odd
RLE4 absolute counts (including their final high nibble) and two-byte deltas.
Nonzero RLE samples in mode 1 become white; single-entry gray palettes reduce to
mode 1. OS/2 palette-offset correction uses three bytes per entry. The decoder writes into
the final bounded raster, without growing an intermediate RLE byte array or
allocating skipped/overshoot pixels. BMP DPI is not inherited by default JPEG
encoding. RGBA bitfields flatten against white through the shared transform.

The processor shares one admission slot/deadline through header parsing, pixels,
transform and encoding. It retains the lazy 200-pixel-bound fast path and original
file fallback for malformed pixels or unsupported JPEG modes; resource limits,
unavailable encoders and cancellation propagate without a successful result.
Positive header/decode/transform limits are mandatory. Header budgets count bytes
actually consumed; pixel budgets count input plus normalized output. These are
logical stage limits, not an aggregate process RSS cap.

`TESTS/adapters/django/login_bmp.py` supplies 534 declared header/pixel/file cases.
Samples determine expected pixels and JPEGs; Pillow's file reader and the actual
Django helper independently verify those outcomes. The shipped non-root,
read-only/networkless image runs the same oracle. Tests include exact budgets,
invalid specifications, input ownership, mid-header/raw/RLE cancellation, 32
concurrent pipelines, queue timeouts and encoder-stage cancellation with child
reaping and admission reuse. BMP/DIB does not complete the multi-format image
service or public login integration; the migration ledger remains implementing.

## PBM, PGM, PPM, PFM and PIL extensions

`ReadPPMHeader`, `DecodePPM` and `PPMFileProcessor` cover the modes registered by
Pillow 11.1's `PpmImagePlugin`: P1–P6, Pf, P0CMYK, PyP, PyRGBA and PyCMYK. The
header parser preserves six-byte magic recognition, ten-byte numeric tokens,
Python signs/underscore syntax, comment joining, exact whitespace consumption and
pixel warning/hard limits. Its positive byte budget includes skipped comments.
PFM scale must be finite and nonzero; its sign selects byte order, while its
magnitude does not multiply samples. PFM rows are bottom-up.

Binary PBM retains row packing/inversion. Binary PGM/PPM scale and clip samples;
plain PGM/PPM reject out-of-range values. Scaling uses the reference's floating
division/multiplication followed by ties-to-even rounding. Mode I and F retain
32-bit little-endian samples and, like palette mode P, fall back to the original
file when JPEG saving is unsupported. CMYK and RGBA use the existing JPEG/white
alpha pipeline. A raw file's bytes immediately after the consumed header are
pixels, including additional whitespace or a comment marker.

Plain decoding retains Pillow's 1 MiB read boundaries. It removes comments before
splitting tokens, carries split numbers between blocks and checks the last partial
token's length before processing complete tokens in that block. Plain PBM validates
all digits in the block even when the image already has enough pixels; numeric
PPM can ignore surplus complete tokens. The comment-end helper's index-zero
CR/LF behavior is retained when a comment continues into another block.

The decoder's logical budget counts input, normalized output and a reusable
bounded block buffer with ten bytes of split-token headroom. Fixed parser state,
allocator overhead and aggregate RSS are outside that accounting. All stages
share the file processor's admission/deadline; cancellation, working-limit errors
and unavailable encoders propagate without a successful result. Lazy passthrough,
malformed-file fallback and the strict 1 MiB resize threshold match the shared
login policy.

`TESTS/adapters/django/login_ppm.py` specifies 429 header/pixel/file cases, anchored
by declared samples rather than copied decoder output. Pillow, the actual Django
helper and the shipped non-root/read-only/networkless image run these cases.
Tests also cover exact header/raw/plain budgets, row arithmetic overflow, input
ownership, cancellation during header/raw/plain/PFM processing, 32 concurrent
pipelines, queue timeouts and encoder-stage cancellation/reaping/admission reuse.
Other formats, shared format dispatch/admission and public login wiring remain open.

## PCX and the first DCX frame

`ReadPCXHeader`, `DecodePCX` and `PCXFileProcessor` follow Pillow 13's
[PCX plugin](https://github.com/python-pillow/Pillow/blob/13.0.0/src/PIL/PcxImagePlugin.py),
[DCX plugin](https://github.com/python-pillow/Pillow/blob/13.0.0/src/PIL/DcxImagePlugin.py),
[RLE decoder](https://github.com/python-pillow/Pillow/blob/13.0.0/src/libImaging/PcxDecode.c)
and planar unpackers. The implementation is pure Go and uses the existing JPEG
encoder. It covers bilevel, two/four bitplanes, 8-bit grayscale/palette and planar
RGB. Palette mode P retains the original-file JPEG fallback.

Header admission accepts the reference's partial headers down to 68 bytes, keeps
its stride correction, and ignores the encoding flag. The 8-bit palette lookup
reads up to 769 bytes from the whole file's end; missing/nonlinear palettes can
change L/P mode. Negative end-relative positions clamp to zero as in Django's
BytesIO input. DCX reads at most 1,024 directory entries and opens only its first
frame; unused later offsets may be invalid. All modes read 68 header bytes and seek to 128 bytes after the first frame
offset, including the grayscale/palette path after its tail-palette lookup. No arbitrary animation playback is claimed.

RLE consumes zero-length runs, rejects runs that cross a row and requires the
complete last row including padding. Each RGB channel or indexed bitplane uses its own stride. Padding is discarded
per plane, matching Pillow 13's corrected unpacking. The previous small-image
compaction and absolute DCX offset quirks are superseded. DPI metadata does not change the default JPEG output.

The logical header budget counts every consumed read, including overlapping
header/palette reads and the DCX directory. Decoding reserves input, normalized
output and one row; it does not expand an unbounded intermediate RLE stream.
One admission/deadline covers the file pipeline through JPEG encoding. Malformed
image/save failures keep the original-file decision; cancellation, budget errors
and unavailable encoders propagate without a successful result. These stage
budgets do not cap aggregate RSS.

`TESTS/adapters/django/login_pcx.py` supplies 831 declared header/pixel/file cases,
checked against Pillow, the actual Django helper and the shipped non-root,
read-only/networkless image. Tests include partial/lazy headers, all run lengths,
palette tails, narrow padded planes, nonzero bounding-box origins, DCX offsets and
directory bounds, file-size thresholds, exact budgets, overflow, input ownership,
mid-header/RLE cancellation, 32 concurrent pipelines and encoder-stage queueing,
cancellation, child reaping and admission reuse. Other image formats and public
login composition remain open; this component does not authorize activation.

Current-reference validation passes all 831 cases with Python 3.13.16/Pillow 13,
the actual Django helper and the non-root read-only/networkless codec image.
Red header, plane-padding and DCX comparisons preceded the correction. Evidence:
`quality-reports/go-migration/security-baseline/pcx-{red,current}.log` and
`pcx-qoi-image.log`. Full image/application reconciliation remains open.

## TGA raw and RLE images

`ReadTGAHeader`, `DecodeTGA` and `TGAFileProcessor` follow Pillow 13's
[TGA plugin](https://github.com/python-pillow/Pillow/blob/13.0.0/src/PIL/TgaImagePlugin.py),
[RLE decoder](https://github.com/python-pillow/Pillow/blob/11.1.0/src/libImaging/TgaRleDecode.c)
and [unpackers](https://github.com/python-pillow/Pillow/blob/11.1.0/src/libImaging/Unpack.c).
The pure-Go path supports raw bilevel, gray, gray/alpha, indexed, 16-bit color,
BGR and BGRA layouts, plus byte-aligned RLE. Both row directions and horizontal
flips are preserved. The high bit of 16-bit color has inverted alpha; descriptor
alpha/interleave bits and origin coordinates do not override reference decoding.

Header admission retains short ID/palette reads and unsupported type/depth
combinations with no pixel tile, including their lazy 200x100 passthrough.
Palette application is a separate load-stage concern: L/LA acquire P/PA core
modes; 32-bit palettes are accepted as RGBA palettes. Incompatible image modes
and oversized palettes still fail. P/PA/LA cannot be saved as JPEG and retain the original file.
The header's `InvalidPalette` records an eventual load failure, not rejection
by the lazy reader. Empty palettes remain meaningful.

Repeated RLE packets cannot cross a row. Literal packets may cross rows or the
end of the image, but their complete declared payload must be present before
any samples are accepted. Finite RLE streams with depth one cannot complete
Pillow's zero-byte sample decoder and fail. Raw rows use packed bilevel bytes
without external alignment padding. For RGBA, a valid TGA v2 footer with a
nonzero extension offset and attribute type zero makes all alpha samples opaque.
Missing/other attributes retain the decoded alpha. This reads only one in-place
attribute byte and does not allocate an extension buffer.

Header budgets count consumed input; implicit zero palette prefixes are not
allocated. Pixel budgets count input plus normalized output; RLE writes directly
to that output without an expanded intermediate stream. The file pipeline has
one admission/deadline through JPEG encoding. Malformed/save failures retain the
original, while cancellation, budget errors and unavailable encoders propagate
without a successful result. These logical budgets do not bound aggregate RSS.

The adapter supplies 1,150 declared header/pixel/file cases, checked with Pillow,
the actual Django helper and the shipped non-root/read-only/networkless image.
Tests also cover exact stage budgets, overflow, input ownership, cancellation
during header/raw/RLE work, 32 concurrent pipelines, and encoder-stage queueing,
cancellation, child reaping and admission reuse. TGA has no fixed magic signature;
its candidate predicate is not a complete cross-format dispatcher. Plugin order,
shared admission, remaining formats and public login composition remain open.

## QOI admission and pixel commands

`ReadQOIHeader`, `DecodeQOI` and `QOIFileProcessor` follow Pillow 13's
[QOI plugin](https://github.com/python-pillow/Pillow/blob/13.0.0/src/PIL/QoiImagePlugin.py).
Only a channel value of 3 selects RGB; every other byte selects RGBA. The reader
consumes 13 header bytes and seeks over colorspace to offset 14 without requiring
that byte to exist. The colorspace value has no effect on output. Pixel limits
and lazy 200x100 passthrough remain part of admission.

The Go decoder implements RGB/RGBA literals, cache indices, difference/luma
commands and runs. The initial previous pixel is opaque black, while all cache entries begin
transparent black. Initial runs do not populate the cache. An empty-index read
replaces previous alpha, affecting following RGB/run commands. Alpha is retained in decoder state and participates in hashing even for
RGB output. RGB literals preserve previous alpha and deltas wrap modulo 256.
Runs can cross rows or exceed the final pixel count; excess pixels are ignored.
The reference does not require or validate a footer. Truncated command operands
fail loading and retain the original file through the login policy.

The logical decoding budget counts input plus the normalized output. The fixed
64-entry cache and parser state are outside that buffer accounting. Runs write
directly into the bounded output without allocating their overshoot. One
admission/deadline covers header parsing through JPEG encoding. Cancellation,
working-limit failures and unavailable encoders propagate without a successful
result; malformed files and ordinary image-save failures keep the original.
Logical budgets are not an aggregate RSS limit.

`TESTS/adapters/django/login_qoi.py` supplies 2,762 declared header/pixel/file
outcomes. Pillow, the actual Django helper and the shipped non-root,
read-only/networkless image run them. Coverage includes every channel/colorspace
byte, every initial cache index and run count, wrapping deltas, luma boundaries,
cache collisions/alpha state, truncation, footer handling and file-size policy.
Exact stage budgets, overflow, ownership, mid-header/opcode/run cancellation,
32 concurrent mixed-image pipelines and encoder-stage queue/cancellation/reaping/
admission reuse are checked. The test-only fault executable now accepts RGB
encoding to exercise that process lifecycle. Production native codec sources are
unchanged. Shared format dispatch/admission, other formats and public login
composition remain open; this evidence does not authorize backend activation.

Current-reference validation passes all 2,762 cases with Python 3.13.16/Pillow 13,
the actual Django helper and the non-root read-only/networkless codec image.
Evidence: `quality-reports/go-migration/security-baseline/qoi-{red,current}.log`
and `pcx-qoi-image.log`; the old seeded-cache behavior is superseded.

## XBM header and text scanning

`ReadXBMHeader`, `DecodeXBM` and `XBMFileProcessor` follow Pillow 13's
[XBM plugin](https://github.com/python-pillow/Pillow/blob/13.0.0/src/PIL/XbmImagePlugin.py)
and [text decoder](https://github.com/python-pillow/Pillow/blob/13.0.0/src/libImaging/XbmDecode.c).
Recognition accepts exact `#define`, newline-plus-`#define`, or block-comment
prefixes. The reader processes at most 75 bytes per line read, ignores supported
block comments, and stops at the first line ending in `_bits[] = {`. Width and
height may be reordered or overwritten; unknown and hotspot defines must still
contain valid Python-style signed/underscored ASCII integers. Pixel text on the
same declaration line is rejected. The earlier 512-byte greedy parser is superseded.

The header budget counts consumed lines, including long comments, and ends at the
selected pixel offset. A checked context accompanies each line read. Numeric
operands are bounded by the 75-byte line size; width/height must pass pixel limits.

Pixel loading is a lowercase-`x` scanner rather than a C parser. Every `x` starts
a pair of operand bytes; hexadecimal letters are case insensitive and all other
operand bytes become zero. No leading zero, braces, commas or closing delimiter
are required. Tokens may span read boundaries; incomplete final operands fail.
Each packed byte contributes its low bit first, with padding discarded at each
row boundary. Surplus text after a complete image is ignored. Normalized bilevel
pixels use the existing nearest-neighbour and JPEG path.

The logical decode budget counts input plus normalized output. Scanning checks
cancellation at most every 4 KiB of skipped text and at each packed byte; it does
not allocate a second text stream or row buffer. One file-pipeline deadline and
admission limit cover parsing through encoding. Malformed/save failures keep the
original; cancellation, resource limits and unavailable encoders return errors
without a successful result. These budgets do not cap aggregate RSS.

The adapter defines 1,509 header/pixel/file cases checked against Pillow, the
actual Django helper and the shipped non-root/read-only/networkless image.
Coverage includes all packed byte values, every possible nibble/macro-name byte,
75-byte line boundaries, comments, first declarations, signed/underscored integers, row padding, truncated/read-boundary
operands and size policy. Exact budgets, overflow, ownership, mid-header/pixel/
long-scan cancellation, 32 concurrent mixed-image pipelines and encoder-stage
admission/cancellation/reaping/reuse pass. Production native sources are unchanged.
Other formats, shared format dispatch/admission and public login remain open.

Current-reference validation passes all 1,509 cases locally, through the actual
Django helper and in the non-root read-only/networkless codec image. Exact logical
budgets and cancellation also cover an 80 KiB comment prefix. Evidence:
`quality-reports/go-migration/security-baseline/xbm-{red,current}.log` and
`xbm-image.log`. Full image/application reconciliation remains open.

## SGI planar and RLE images

`ReadSGIHeader`, `DecodeSGI` and `SGIFileProcessor` follow Pillow 11.1's
[SGI plugin](https://github.com/python-pillow/Pillow/blob/11.1.0/src/PIL/SgiImagePlugin.py),
[RLE decoder](https://github.com/python-pillow/Pillow/blob/11.1.0/src/libImaging/SgiRleDecode.c)
and sample unpackers. The eight supported byte-depth/dimension/channel layouts
produce L, RGB or RGBA. Both raw planar and RLE rows run from bottom to top;
16-bit samples retain only their high byte. Raw planes use the absolute 512-byte
start even when the admitted header is shorter. Headers as short as 12 bytes and
unknown compression values retain lazy 200x100 passthrough; unknown compression
fails when pixels are actually requested. Header metadata does not change the
default JPEG output.

RLE tables may reference shared, overlapping, unordered or unused row data. The
reference treats declared lengths as signed 32-bit packet-loop counts, not slices
of encoded bytes. Zero/negative counts leave row storage unchanged; offsets below
512 still fail, while unconsumed past-file offsets are tolerated. A nonzero opcode
with one declared packet remaining exits successfully before storing that row,
leaving subsequent output zero. Partial rows retain the preceding source row's
unwritten samples and bands. Literal/repeat bounds preserve the reference's
last-byte end pointer, including 16-bit control-byte and terminator behavior.

The decoder reads tables directly from the input and uses one persistent
normalized row for RLE. Logical budgets count input plus output, and that row for
RLE; they do not cap aggregate RSS. Raw decoding needs no separate plane buffer.
One admission/deadline covers the full file pipeline. Malformed/save failures keep
the original; cancellation, resource errors and unavailable encoders propagate
without a successful result.

The adapter supplies 1,294 declared header/pixel/file cases, checked against
Pillow, the actual Django helper and the shipped non-root/read-only/networkless
image. Coverage includes all supported layouts, partial/lazy headers, all run
lengths, row/table bounds, shared/overlapping/reordered offsets, row carry, signed
lengths, early successful exit and size policy. Exact header/raw/RLE/stage budgets,
overflow, ownership, mid-header/planar/RLE cancellation, 32 concurrent mixed-image
pipelines and encoder-stage admission/cancellation/reaping/reuse pass. Production
native sources are unchanged. Remaining formats, shared dispatch/admission and
public login composition remain open.

## SUN Raster images

`ReadSUNHeader`, `DecodeSUN` and `SUNFileProcessor` follow Pillow 11.1's
[SUN plugin](https://github.com/python-pillow/Pillow/blob/11.1.0/src/PIL/SunImagePlugin.py),
[byte-RLE decoder](https://github.com/python-pillow/Pillow/blob/11.1.0/src/libImaging/SunRleDecode.c)
and sample unpackers. Supported depths are 1, 4, 8, 24 and 32. Monochrome samples
are inverted and MSB-first; luminance nibbles scale by 17, while palette nibbles
remain indices. Type 3 stores RGB/RGBX; other types store BGR/BGRX. The fourth
channel is ignored. Types 0, 1, 3, 4 and 5 use raw rows aligned to 16 bits; the
last row does not require trailing alignment bytes. Type 2 uses byte RLE with
packed row lengths, without the raw alignment. An escaped zero denotes literal
0x80; other escape counts repeat the following byte count+1 times. Runs can cross
rows and overshoot the image; incomplete operands fail while pixels remain.

The 32-byte header ignores its data-length field. A nonzero palette length must
be at most 1024 with palette type 1; zero length ignores palette type. Palette
reads may be short, but pixels still begin at the declared offset. L becomes P;
palettes attached to other core modes fail at loading. More than 256 complete
planar palette entries also fail at loading; up to two trailing bytes are
accepted. Lazy size-policy passthrough occurs before these deferred failures.

Logical decode budgets count input plus output, with one packed row for RLE;
they do not cap total RSS. Raw pixels are unpacked directly into output. Stage
budgets, a shared pipeline slot and one deadline cover header/decode/transform/
encoding. Malformed or unsupported-save cases keep the original; resource,
cancellation and unavailable-codec failures expose no successful result.

The adapter declares 1,453 header/pixel/file cases, including every literal and
run count, row packing/orientation, palette/core-mode and partial-read boundaries,
64 KiB RLE operand boundaries, malformed headers/operands and size policy. It
compares declared pixels and JPEG bytes with Pillow, the actual Django helper and
the shipped non-root/read-only/networkless image. Unit/integration checks cover
exact budgets, overflow, ownership, mid-operation cancellation, 32 mixed concurrent
pipelines and encoder-stage queue/cancellation/reaping/reuse. Native production
sources are unchanged.

This is a SUN-specific processor, not a global format dispatcher. Pillow's
registry can recognize some SUN-signature inputs as GBR first: SUN width 1,
height 1, depth 8 and declared data length 1 is recognized as GBR with size 1x8.
The ignored-length SUN vectors use width 3 to isolate SUN behavior. Registry
precedence, including this collision, must be preserved by the future shared
dispatcher; remaining formats and public login composition remain open.

## GIMP brush images

`ReadGBRHeader`, `DecodeGBR` and `GBRFileProcessor` follow Pillow 11.1's
[GBR plugin](https://github.com/python-pillow/Pillow/blob/11.1.0/src/PIL/GbrImagePlugin.py).
Version 1 requires 20 fixed header bytes; version 2 requires 28, including `GIMP`
and an ignored spacing field. Depth 1 produces L and depth 4 produces RGBA.
Pixels are a top-to-bottom packed block with no row alignment; truncation fails
and trailing data is ignored. RGBA uses the common white-composition path.

The declared header size must be at least 20, including version 2. A version-two
size below 28 causes a negative Python read length and therefore consumes the
remaining input. Other comment reads stop at EOF. The final byte actually read
is always discarded from the comment, without requiring a NUL terminator.
Pixels begin at the actual consumed position, not a potentially past-file
header size. The Go header records spans in the original input and allocates no
comment buffer. Header budgets charge consumed bytes; decode budgets charge
input plus independently owned output. These are logical bounds, not total RSS.

Nonempty grayscale comments survive JPEG encoding; RGBA white composition drops
metadata. Empty comments omit the JPEG COM marker, matching Pillow. The shared
Go JPEG encoder now applies that rule to both nil and empty byte slices. The GBR
oracle exposed the previous difference; production native code is unchanged.
Lazy size-policy passthrough precedes pixel loading and comment-save failures.

The adapter declares 1,341 header/pixel/file cases, including both versions and
sample modes, every byte value, short/negative comment reads, arbitrary comment
terminators, JPEG comment limits, truncated pixels, ignored spacing, size policy
and a SUN-signature input recognized as GBR by Pillow. Cases compare declared
pixels and JPEG/original decisions with Pillow, the actual Django helper and the
shipped non-root/read-only/networkless image. Checks include exact stage budgets,
overflow, ownership, mid-operation cancellation, 32 mixed concurrent pipelines
and encoder-stage queue/cancellation/reaping/reuse. Shared registry dispatch and
public login composition remain open.

## Windows Paint MSP images

`ReadMSPHeader`, `DecodeMSP` and `MSPFileProcessor` follow Pillow 11.1's
[MSP plugin](https://github.com/python-pillow/Pillow/blob/11.1.0/src/PIL/MspImagePlugin.py).
Its source credits the Windows Paint format description in O'Reilly's
*Encyclopedia of Graphics File Formats*. The fixed 32-byte header must have a
zero XOR across all sixteen little-endian words. `DanM` selects raw packed
samples and `LinS` selects RLE. Both produce mode 1, with MSB-first white bits
and byte-aligned rows; unused header words do not affect JPEG output.

LinS begins with one unsigned 16-bit encoded length per declared row. A zero
length contributes a white packed row. Other records contain literal packets
or zero-prefixed count/value runs. A literal slice clips at the record boundary;
a repeat requires both operands even when its count is zero. Expanded record
lengths do not have to equal a packed row: Pillow concatenates them before raw
unpacking, so one record can supply pixels for another. Too few total packed
bytes fail. Surplus expanded bytes are ignored, but every declared record and
repeat operand is still validated. Data after the declared records is ignored.

Go streams the packed samples directly into normalized output and retains no
expanded intermediate buffer. Logical decode budgets count input plus output,
not total RSS. Arithmetic and rowmap bounds are checked before allocation;
cancellation remains observable while validating surplus records. Header errors,
malformed pixels and save failures retain the original; resource/cancellation/
unavailable-codec errors expose no successful result. One admission/deadline
covers the complete file processor.

The adapter declares 1,704 header/pixel/file cases, compared with Pillow, the
actual Django helper and the shipped non-root/read-only/networkless image. It
covers every packed byte and run/literal count, row padding, blank and
concatenated records, clipped literals, malformed surplus operands, checksum,
short headers/maps/payloads, ignored metadata/tails and size policy. Exact budgets,
overflow, ownership, raw/RLE/surplus cancellation, 32 mixed concurrent pipelines
and encoder-stage queue/cancellation/reaping/reuse pass. Production native code
is unchanged. Remaining formats, shared dispatch/admission and public login
composition remain open.

## GIF first-frame images

`ReadGIFHeader`, `DecodeGIF` and `GIFFileProcessor` implement the first-frame
path from Pillow 11.1's [GIF plugin](https://github.com/python-pillow/Pillow/blob/11.1.0/src/PIL/GifImagePlugin.py)
and [LZW decoder](https://github.com/python-pillow/Pillow/blob/11.1.0/src/libImaging/GifDecode.c). Global/local identity
palettes produce L; colored palettes produce P. A retained global colored palette
also makes a locally grayscale frame fail JPEG saving during resizing. Those
images preserve the original URI. Grayscale frames use the common transformation
and JPEG encoder, retaining joined comment extensions. Transparency fills the
initial canvas with the declared index; it does not introduce RGBA conversion for
this first-frame loading strategy. Offsets can enlarge the logical canvas.

The bounded LZW decoder supports initial widths 0 through 12, clear/reset,
self-reference, dictionary growth/saturation and four-pass interlacing. It requires
a whole declared subblock before reading it and finishes at the last required
pixel without demanding a trailer/end code. A premature end code returns control
to Pillow's 64 KiB input loop; even buffered data cannot resume without another
read. The oracle exposed and verifies that boundary. Subsequent animation frames
are ignored. Malformed files and JPEG save failures retain the original; resource,
cancellation and unavailable-codec failures propagate without a successful result.

Header budgets charge consumed bytes plus retained comments. Decode budgets charge
input, output and the fixed 16 KiB dictionary/stack. These are logical payload
bounds, excluding allocator capacity/overhead and total RSS. One deadline and
admission slot cover header, decode, transformation and encoding. Tests verify
exact stage limits, independent output/comment storage, overflow rejection,
cancellation during scanning/LZW/fill, 32 concurrent mixed files, queued deadlines
and encoder cancellation/reaping/reuse.

`TESTS/adapters/django/login_gif.py` declares 644 header/pixel/file cases and checks
the actual unmodified Django helper, Pillow and Go. Authored literals and explicit
sample expectations anchor the oracle; Pillow-written inputs additionally exercise
long dictionary chains and resets. Tests run inside the shipped non-root,
read-only, networkless image as well. Native codec sources are unchanged.
The shared processor below now supplies admission and partial registry dispatch.
Other formats and public login remain open.

## Shared data-URI processor — partial registry

`NewProcessor` requires the trusted codec path and explicit URI, file-stage,
concurrency and deadline limits. One slot is acquired before Base64 decoding and
held through format selection, pixels, native children and final Base64 encoding.
All formats share that bound. Nested file deadlines cannot extend the outer
request deadline. Input/output URI bytes are bounded independently of decoded
file/raster/header budgets; allocations and caller-owned queued inputs are not a
total RSS guarantee. Cancellation/resource/codec failures return no URI.

Malformed Base64 and definitively unrecognized data preserve the original string.
The media-type prefix is opaque; successful conversion always emits the JPEG
prefix. Lazy passthrough and malformed-file fallback come from the existing file
pipelines. A formatted JPEG exceeding the URI budget fails before output allocation;
encoding checks cancellation between bounded chunks.

Registry order follows Pillow 13's default `preinit()` followed by `init()`,
including the AVIF candidate immediately after PNG.
GBR identification failures now fall through to later QOI/SUN readers; GBR hard
pixel-limit failures terminate opening and preserve the original. Empty CUR
containers do not shadow valid TGA files. Known-format overlap is checked before
choosing a pipeline. Other readers still collapse recoverable and terminal opening
exceptions; those unresolved collisions fail explicitly instead of selecting an
unproven interpretation. External plugin registration/reordering is unverified.

Missing Pillow formats retain candidate guards, including no-signature text and
scientific formats. These guards are deliberately conservative: some malformed or
ambiguous data that Django preserves may currently fail in Go. TIFF and other missing codecs must still be implemented; the demonstrated
TIFF fixture gap is **not an exhaustive list**. Do not wire this into a deployment
and claim complete image or public-login compatibility.

`TESTS/adapters/django/login_images.py` asserts 94 whole-URI expectations against
the actual Django helper and the composed Go processor. Eighteen pipelines,
alpha handling, opaque prefixes, the first comma, permissive Base64, malformed
files, the 1 MiB size boundary and registry collisions are exercised. Separate TIFF and AVIF
missing-codec cases deliberately report `complete_image_parity=false`; it is
never counted as passing parity. The shipped-image test includes both sets.

Race/integration tests mix formats through one processor, enforce a two-child
bound across different codecs, cancel running/queued work, check deadline and exact
URI-output limits, and prove reaping/slot reuse. The PostgreSQL response adapter
now injects this concrete processor: 48 response cases include twelve image cases,
32 mixed concurrent responses, unchanged database snapshots after declared card/
audit resets, and a missing-decoder failure with no response tokens or card query.
No public HTTP login, full registry coverage or backend activation is established.


## ICO selected-frame pipeline

`ICOFileProcessor` follows Pillow 11.1.0's `IcoImagePlugin`, using the existing
bounded PNG and DIB decoders. It scans every 16-byte directory entry without
retaining a per-entry allocation. Selection is by largest declared area, then
**lowest** color depth, retaining directory order for ties. Zero dimensions mean
256; zero bit depth uses the palette-count rule, including the one-color fallback.
The selected frame's actual dimensions replace the directory dimensions.

ICO loads its selected image eagerly, even when the login size policy ultimately
returns the original URI. A PNG frame uses the complete first-frame decoder and
post-pixel parsing; its comment and other metadata do not become ICO metadata.
A DIB frame checks the original double-height header before halving its height.
It uses existing raw/RLE/bitfield decoding, expands palette samples to RGBA, and
applies the alpha mask. Missing palette entries are black. For directory depth 32,
alpha comes from every fourth byte at the DIB pixel offset, always bottom-up even
for top-down RGB rows. Other depths use the inverted, padded one-bit AND mask at
`frame_offset + declared_size - mask_size`. PNG decoding ignores declared size.
No cursor support is inferred from ICO support.

The processor bounds encoded input, cumulative directory/embedded-header reads,
PNG tail work, decoded pixels and alpha buffers, transform buffers, native codec
concurrency and deadlines. The decode budget counts the full original input;
DIB alpha composition additionally charges the decoded raster and new RGBA raster.
These are logical stage limits, not a total RSS guarantee. Cancellation and
resource/native-codec failures return neither JPEG bytes nor a passthrough result.
Malformed image content retains Django's original-URI fallback. Directory scanning
and alpha composition check cancellation within bounded work chunks.

`TESTS/adapters/django/login_ico.py` has 678 authored pixel/file expectations checked
against actual Pillow and the unmodified Django helper. They cover PNG modes,
metadata dropping, directory selection, actual dimensions, 1/4/8/16/24/32-bit DIB,
all alpha values and AND bit patterns, top-down color/bottom-up alpha, all supported
DIB header sizes, odd heights, bitfields, RLE, partial palettes, truncated directory/
PNG/masks and the whole-file 1 MiB threshold. Existing PNG/DIB component oracles
remain applicable; the ICO-specific sample set does not prove every possible
container/plugin interaction. The shared registry still reports unresolved opening
collisions explicitly.

Go tests additionally cover exact header/decode bounds, unchanged input ownership,
eager-decoding limits on unchanged-size images, absent native executables, 32 mixed
concurrent calls, mid-scan/mid-alpha cancellation, and native PNG/JPEG cancellation,
queue expiry, reaping and slot reuse. The accounts and shipped-image adapters include
this oracle. Shared URI parity now has 70 cases and three demonstrated, non-exhaustive
gaps. Forty-four PostgreSQL response cases include a concrete ICO logo conversion.
Public login, remaining formats and backend activation remain unfinished.


## WebP native first-frame pipeline

`WebPDecoder` uses the isolated helper's `webp-info` and `webp-decode` operations.
Like [Pillow 11.1's WebP binding](https://github.com/python-pillow/Pillow/blob/11.1.0/src/_webp.c),
it uses `WebPAnimDecoder` for static and animated files. Feature inspection selects
RGB or RGBA; default animation decoding returns a complete first-frame canvas,
including frame offsets and alpha. The parser validates the container before
reporting dimensions. Pixel corruption may remain deferred until decoding.
`WebPFileProcessor` inspects first, applies the login size policy, and decodes pixels
only when conversion is needed. ICC/EXIF/XMP do not become JPEG encoder options.

A single file slot and outer deadline cover inspection, decoding, transform and
JPEG output. There are at most two WebP child invocations and one JPEG child per
conversion. No native decoder is shared across requests. Input and output are
bounded and cancellation kills/reaps the current child before releasing admission.
Malformed native input returns the original URI; resource/cancellation/unavailable
codec errors return no file result. Valid WebP signatures require the native parser
even for unchanged-size passthrough. Shared registry collisions remain a separate,
incomplete gate; native format support does not prove all registry interactions.

The helper preflights demuxed dimensions before constructing `WebPAnimDecoder`,
which allocates two RGBA canvases. The explicit logical native budget includes the
whole input, both canvases and a packed RGB row when needed. It also enforces the
Pillow hard pixel ceiling. Demux/codec internal allocations, allocator overhead and
combined process RSS are not covered by this logical budget. Deployment budgets
and supported-data sizing remain unverified while public login is unwired.

The `AZWP` protocol has a fixed 32-byte header: magic; big-endian width and height;
channel count plus three zero bytes; frame count, loop count and ARGB background;
and four zero bytes. Decode responses append exact packed RGB/RGBA samples.
The Go reader validates dimensions, reserved bytes, mode, counts and exact length.
Neither a failed child nor an invalid frame exposes partial output. The native
helper accepts only exact declared stdin length and emits no malformed-input result.

`TESTS/adapters/django/login_webp.py` has 178 declared header/pixel/file cases:
lossless RGB/RGBA, independently declared lossy grayscale values, transparent pixels,
animated first frames and partial canvases, ignored metadata, truncated RIFF data,
deferred entropy errors and whole-file size boundaries. Every case checks actual
Pillow and Django. Native failure doubles verify malformed/oversized output,
missing executables, sanitized environments, queue expiry, cancellation at parser,
decoder and encoder stages, reaping and reuse. Thirty-two concurrent calls and
exact stage budgets are checked. The PostgreSQL response fixture has 45 cases,
including WebP avatar/logo conversion; TIFF still proves unsupported-image bypass
and no-token/no-card-query failure behavior.

The shipped helper links static libwebp and distributes its BSD license, patent
grant and exact package version. The image test checks those files and the binary
version. The same oracle passes against local libwebp 1.6.0 and Alpine 3.21's
libwebp 1.4.0, with Pillow's libwebp 1.5.0 reference. This is evidence for the declared
cases, not a blanket equivalence claim across library versions. The WebP wrapper
passes 380 exact frame/error/protocol ASan/UBSan checks, including allocation bounds
before a 100-million-pixel canvas; linked system libraries are not rebuilt with
sanitizers. The existing PNG sanitizer suite also passes with the combined helper.
Shared URI tests now report 72 parity cases and two demonstrated missing-codec
gaps; that census remains non-exhaustive. Public login and remaining formats stay open.


## CUR directory and lazy DIB pipeline

`CURFileProcessor` follows Pillow 13's `CurImagePlugin` and reuses the existing
DIB parser and raw/RLE sample decoder. Selection replaces the current entry only
when **both** directory width and height are strictly larger. Directory zeros are
literal zero values. Hotspots, color-count and declared byte size do not control
decoding. The selected DIB supplies the actual dimensions. A zero offset continues
from the cursor after directory reads; overlapping or ignored partial entries
retain Pillow's behavior.

CUR halves the DIB height before applying the image pixel ceiling. Odd heights
truncate; zero final dimensions reject opening. The parser still validates bit
fields, compression and palette before this final dimension check. For raw 32-bit
DIB data, only a declared offset of 22 selects BGRA/RGBA; other offsets use BGRX/RGB.
CUR does not accept embedded PNG. For grayscale DIB modes 1/L, `DecodeCUR`
decodes both halves and applies the top AND mask to the bottom XOR intensity,
returning LA. Odd masked heights or missing plane bytes fail pixel loading.
The login JPEG path preserves these LA originals. Other modes keep the existing
half-height decoding; their trailing mask is ignored. DIB alpha follows row orientation. Pixel loading remains lazy when the login policy returns
the original file; corrupt pixel payloads can therefore remain unchanged.

`ErrCURRejected` identifies recoverable opening failures such as incomplete
struct words, empty directories and zero final dimensions. `ErrCURTerminal`
identifies terminal failures such as truncated DIB blocks, unsupported encodings
and the hard pixel ceiling. Both wrap `ErrCURHeader`. Shared dispatch uses these
categories to preserve CUR/TGA precedence; it does not treat a terminal CUR failure
as permission to try TGA. Other formats' unresolved registry exceptions remain open.

One file admission slot and deadline cover directory inspection, DIB decoding,
transformation and JPEG output. Header budgets charge bytes actually consumed,
including repeated reads of overlapping data. Decode budgets charge the whole
input and the normalized raster; masked CUR also charges both source planes. Existing transform and JPEG budgets apply before
allocation/output. Directory/palette/pixel loops observe cancellation. These are
logical buffer budgets, not total-process RSS or production sizing guarantees.

`TESTS/adapters/django/login_cur.py` checks 533 independently declared cases against
Pillow and the actual Django helper: selection, offsets, all raw alpha values,
core/v1-v5 DIB headers, palettes, RLE, bitfields, orientation, grayscale masks/alpha, ignored non-gray masks, lazy
passthrough, file-size boundaries and exact opening-failure categories. Separate
header-only boundary checks validate halving before the pixel ceiling without
allocating huge rasters. Go checks exact resource boundaries, output ownership,
pre-cancellation and directory cancellation, 32 concurrent calls, queue deadlines,
JPEG child reaping and pipeline reuse. The existing 534 BMP and 678 ICO oracle
cases pass with the shared parser change. The shared dispatcher has 88 URI parity
cases and two separately reported, non-exhaustive gaps. The PostgreSQL fixture
contains 48 responses, including CUR avatar/logo conversion. Public login and
complete image/backend parity remain unverified.


## PCD PhotoYCC pipeline

`PCDFileProcessor` follows Pillow 13.0.0's `PcdImagePlugin` and its PhotoYCC
unpacker. The header seeks to byte 2048, reads up to 1539 bytes, checks `PCD_`,
and masks the orientation byte at offset 3586 with 3. The source raster is
768×512 RGB, with 512×768 output for orientations 1 and 3. Pixel blocks start at byte 196608: two 768-byte luma rows followed
by 384-byte Cb and Cr rows. Each chroma sample applies to a two-by-two cell.
Five attributed Pillow lookup tables preserve signed rounding and clipped RGB;
no new native decoder dependency is introduced.

Orientation 2 leaves pixels unchanged. Orientations 1 and 3 rotate 90 and 270
degrees respectively, matching Pillow 13's corrected rotation hook. The earlier
Pillow 11 behavior that retained original files on rotation failure is superseded.
Go maps each decoded pixel directly into the rotated output without a second RGB
allocation. Files at most 1 MiB convert at their oriented dimensions; larger files
resize to 200×133 or 133×200. Truncated pixels retain the original. Header rejection
can fall through to QOI, while an accepted PCD with failed pixels must retain the
original instead of selecting QOI.

Header budgets charge bytes read after the seek. Decode budgets cover input plus
1,179,648 RGB bytes before allocation. Input, transform, encoder and outer URI
limits remain independent logical bounds, not an aggregate RSS guarantee. The
pipeline checks cancellation during row-pair decoding and shares one admission
slot and deadline through JPEG encoding. Cancellation/resource failures expose no
result; fallback only applies to the declared image/encoding failures.

`TESTS/adapters/django/login_pcd.py` declares 63 cases: seven fixed RGB anchors,
independent arithmetic luma/chroma sweeps, chroma-cell layout, all masked
orientations, truncated headers/pixels, bad signatures and exact 1 MiB boundaries.
Pillow and the unmodified Django helper check those expectations. Go tests cover
exact budgets, ownership, cancellation, 32 mixed calls and native encoder reuse.
A fixed-size fault child stops before reading stdin, verifying cancellation and
reaping while the 1.18 MiB raster writer is blocked. The shared dispatcher has
94 URI expectations and separately reported, non-exhaustive TIFF and AVIF gaps. The
PostgreSQL response fixture includes PCD avatar and rotated-logo conversion.
The current-reference shared dispatcher and 48 response cases pass. Public login
and complete image coverage remain unfinished.


The production-Python 3.13.16 / Pillow 13.0.0 focused oracle passes all 63 cases
including independently indexed quarter-turn gradient pixels and both resized
orientations. Red integration and exact-header-budget tests preceded the fix.
Evidence: `quality-reports/go-migration/security-baseline/pcd-{red,current}.log`.

## PIXAR raw RGB pipeline

`PIXARFileProcessor` follows Pillow 11.1.0's `PixarImagePlugin`: a four-byte magic,
a read of at most 512 header bytes, little-endian height/width at offsets 416/418,
and the only supported channel/depth pair `(14, 2)` at offsets 424/426. A short
header is valid from byte 428 onward. Unsupported modes and zero dimensions reject
the candidate before the hard pixel ceiling; that ceiling terminates registry
lookup. Other header bytes and padding do not change the raster interpretation.

Pixels are packed RGB rows at offset 1024, copied into owned output in cancellable
4 KiB chunks. Decode budgets cover input plus output before allocation. Header,
input, transform, native encoder and outer URI limits remain independent logical
bounds, not a process RSS guarantee. The file processor keeps one admission slot
and deadline through transformation and JPEG output. Lazy size-policy passthrough
can preserve a file with no pixels; malformed pixels and unsupported JPEG dimensions
also retain the original. Large files with a zero-sized resize target fall back.
No native decoder or new runtime dependency is needed.

`TESTS/adapters/django/login_pixar.py` declares 81 header/pixel/file cases against
Pillow and the actual Django helper. RGB samples are authored independently.
Cases include raw refill boundaries, ignored padding/trailing bytes, short reads,
mode/dimension rejection, JPEG dimension limits, lazy missing pixels and exact
1 MiB thresholds. Unit/integration tests check exact budgets, ownership, forged
headers, cancellation, native queue/deadline/reaping/reuse and 32 mixed calls.
Shared dispatch has 94 URI comparisons and separately reported, non-exhaustive
TIFF and AVIF gaps. PCD header rejection can expose PIXAR; an accepted PCD with bad pixels must
retain the original instead. The PostgreSQL response fixture adds PIXAR avatar
conversion and lazy logo passthrough. Full image and public-login parity remain open.
