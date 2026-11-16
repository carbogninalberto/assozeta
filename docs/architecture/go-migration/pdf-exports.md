# PDF exports — a decoded-document parity standard and a Go writer

Django builds a few documents in-process with reportlab 4.2.5 (pure Python, no accelerator):

| Code path | Document | Views |
| --- | --- | --- |
| `generate_pdf_from_data` (`application/utils/printing.py`) | one table of strings, title and page number on every page | `printing/generate` (`pdf`), `subscription/list`, `course/list` and `course-subscriptions/list` POST `format: pdf` |
| `generate_pdf_from_data` with `header` and `additional_data` | header table (logo, three paragraphs), table, closing paragraph | `balance-sheet` POST `format: pdf` |

`BE_GO/internal/pdfexport` writes the same documents.

## The parity standard

A reportlab document is not byte-reproducible as a whole: the Info dictionary holds the creation time, the trailer `/ID` is
`md5('a reportlab document' + repr(time.time()) + the default Info strings)`, and the page streams are deflated (the
compressed bytes depend on the zlib build; Go's `compress/flate` is another implementation). Everything else is
reproducible, so parity is checked on the decoded document:

- the same header, the same objects in the same order and the same trailer, byte for byte, once every stream is decoded
  (ASCII85, then Flate where the object names it) and its `/Length` is set aside. This covers the page size, the fonts, every
  content stream operator with its operands (text runs and their positions, rules, fills, colours), the Info strings, the
  page tree and the object numbering;
- `/CreationDate` and `/ModDate` equal each other, are written in UTC (Django's `TIME_ZONE`) and fall within the request
  window (start truncated to the second);
- the two halves of the trailer `/ID` are the same 32 hex digits;
- an embedded JPEG is compared by its bytes, its dictionary and the places it is used; its object name is numbered by first
  appearance on both sides (see "The writer");
- each file is checked for consistency on its own: the cross-reference table lists every object at its real offset, every
  stream ends at its `/Length`, `/Size` counts the objects and `startxref` points at the table. The offsets and lengths
  themselves follow the compressed bytes and are not compared.

Opt-in for contracts: `TESTS/contracts/pdf.py`. `{'$pdf': spec}` names the document by the inputs the view hands reportlab;
the helper builds the expected document with the same library and the same calls, and `resolve(expected, actual, started,
finished)` replaces a matching document by the received value. Its controls are
`python -m unittest TESTS.contracts.test_pdf_standard` (equal documents pass at any deflate level; a dropped column, a wrong
total, a text run moved by 0.001 point, a missing rule, a colour, a font, an extra page, a date outside the window or in
another zone, and every inconsistent file fail).

## The writer

`pdfexport.DataTable{Columns, Rows, Title, Align, Header, IsA3, Portrait, Additional}.Build(now)` is
`generate_pdf_from_data`:

- column widths `max(len(str(value))) * (11 / 12) / 3.5` cm with a minimum of 1 cm (`None` is measured as `None` and drawn
  empty), the smallest of A4, A3 and A2 the table fits in (A2 when none does), landscape unless the caller asks otherwise;
- `SimpleDocTemplate` with 30-point margins: one frame per page with 6 points of padding, the title (Helvetica-Bold 9) and
  `Pagina <n>` (Helvetica 9) drawn before the frame's content on every page;
- `platypus.Table`: row heights from the number of lines of each cell (leading 12, padding 3 + 3, 6 below the header), the
  compensated sum of the row positions, the split between rows when a page is full with the BOX and GRID commands rewritten
  as reportlab rewrites them for each part, backgrounds, text (centred, left or right, bottom-aligned, one `Tj` per line of
  a cell) and rules in reportlab's order;
- text in the standard fonts: WinAnsi bytes with reportlab's escapes, characters outside the encoding through the Symbol and
  ZapfDingbats substitution fonts with the font switches reportlab writes, and the ZapfDingbats `n` for the rest; widths
  from the fonts' AFM tables (`fonts_gen.go`, generated from reportlab's own tables), numbers through `fp_str`;
- the file: font dictionary, fonts in order of first use, pages, catalog, Info, page tree, one ASCII85 + Flate stream per
  page, cross-reference table, trailer with the `/ID` computed as reportlab computes it.

With `Header` (the balance sheet) the document starts with reportlab's header table and ends with the `Additional`
paragraphs:

- the header is read with the view's own patterns (`src="data:image/jpeg;base64,…"`, `<h1>`, `<h2>`, `<h3>`, with their
  fallbacks) and laid out as a one-row table of four fixed columns as wide as an A2 sheet together (it is wider than the
  page, as in Django), top-aligned with 20 points above and below;
- `platypus.Paragraph` for plain text: reportlab's whitespace cleaning, line breaking with the 5 % space shrinkage, long
  words split character by character, left-aligned or centred lines in one text object (`Td` offsets, `Tw` for a shrunk
  line), `spaceBefore`/`spaceAfter` inside a cell and in the frame, and a paragraph that moves to the next page when
  fewer than two lines fit, or is cut there when more do;
- `platypus.Image` of a JPEG: the file is embedded as it is (ASCII85 + DCT) with its size and colour space, drawn through
  a form object listed in the resources of the page that uses it. reportlab names the object `FormXob.<md5 of the RGB
  pixels PIL decodes>`; another JPEG decoder does not give the same pixels bit for bit, so Go names it after the file's
  digest and the standard numbers image names by first appearance on both sides. Everything else about the image (bytes,
  dictionary, placement) is compared exactly.

Refused (`ErrNotPorted`, the callers answer the 500 page before any write): the paragraph tags whose drawing is not ported
(see "Paragraph markup"), and logo data that is neither refused by Python's base64 nor a JPEG Go's decoder reads (whether PIL
reads it, and as what, is not reproduced).

The refusal sites, and why the ones left stay (s3pdfcanvas):

| Site | Condition | Class | Why |
| --- | --- | --- | --- |
| `widths.go` `preliminaryWidths` (2 returns) | a table without column widths holding a string cell, or a cell whose flowables are not paragraphs | C | no builder of this package makes one: the data table, the header table and the attendance table pass every width, and the saved-report table is Paragraph cells only; a split keeps `colWidths` |
| `header.go` `loadJPEG` (2 returns) | the base64 logo is not a JPEG `image/jpeg` reads | C | the logo rows are written by `compress_base64`, which re-encodes to JPEG and prefixes `data:image/jpeg`; the header pattern only looks at that prefix |
| `header.go` (2 returns) | a decoded JPEG with another colour model, or a zero dimension | C | `image/jpeg` yields only grey, YCbCr, RGBA and CMYK, always with positive dimensions |
| `paraparser.go` `unsupported` | `bullet`, (`seq*` ported in long tail PR 3), `<a name>`/`#`/`document:` links, `img` of a file/URL/PNG, `<unichar name>`, CMYK/alpha/expression colours, `inf`/`nan`, `<para>` indents, justification, borders, text transforms, `autoLeading`, `wordWrap`, end dots, hyphenation | B (X for `seq*`, file/URL `img`, expressions, hyphenation) | Django draws them (or, for `seq*`, numbers from a process-global sequencer); each needs a layout, a font, a destination tree or an image format this package does not carry yet |

A document reportlab fails to build is `ErrLayout`, which each caller answers as Django answers an exception of the export:
a row taller than a page (`LayoutError`), a list value in a cell (reportlab takes it for a list of flowables), rows of
another length than the columns, an invalid alignment, paragraph markup the parser raises on.

## Paragraph markup

Every paragraph text goes through reportlab's `ParaParser`, an `html.parser.HTMLParser` with character references left
alone. Go ports both:

- `htmlparser.go` is CPython 3.13's `HTMLParser.goahead(0)`: start and end tags with the tolerant attribute patterns (their
  backtracking included), comments, declarations, processing instructions, CDATA sections, the raw-text elements (`script`,
  `style`, `title`, `textarea`, `plaintext` and the others), entity and character references with their terminators, and
  the incomplete construct at the end that a feed without `close()` leaves unread;
- `paraparser.go` is the tag stack: `<b>`, `<strong>`, `<i>`, `<em>` choose the bold and oblique Helvetica fonts, `<br/>`
  breaks the line, unknown tags are ignored, the 2121 entity names reportlab knows are replaced (`entities_gen.go`), an
  unknown one is drawn as written. A tag closed out of order or left open, text inside `<br>`, a character reference beyond
  U+10FFFF and the other inputs the parser raises on are `ErrLayout`: the export fails as in Django (`A<B` opens a tag `b`
  that is never closed). The tags that draw lines, links, images, rises, other sizes and colours are ported (t3pdf, below);
  `greek` (the Symbol font and its byte-to-character table, `fonts_std_gen.go`), `unichar` (a character by its `code`),
  the Courier and Times faces (their width vectors and ascents), the decimal digits of other scripts in numbers, and the
  `<index>` and `<onDraw>` callbacks are ported in s3pdfcanvas: those two parse, then fail the document the way reportlab
  fails it in a `SimpleDocTemplate` (its canvas has no `_indexAdd` or callback attribute), so they raise the same exception
  text. The `seq*` tags are ported in long tail PR 3 (`sequencer.go`: one process-wide sequencer, as reportlab's
`getSequencer()`; 53 paragraph texts checked against reportlab 4.2.5; a template that prints the sequencer object
itself stays refused). Long tail PR 4 (agent B): an `img` `src` is read as reportlab's `ImageReader` reads it
(`open_for_read`: the name as a path relative to the working directory, else a `data:` URL, else `urlopen`; any failure
is its `Cannot open resource` OSError with the reader's identity), through `ResourceOpener` (`resource.go`), and a
template that prints the sequencer object answers its repr; both object addresses are `pystr.ObjectAddress` (approved
divergence X18). Still refused: `bullet`, `<a name="..."/>` anchors and `#name` or `document:name` links (named destinations
  are not ported), `img` whose image is not a JPEG (a PNG or other format is re-encoded by PIL) or whose `src` is an `ftp:`
  URL, `<unichar name="...">` (Python's `unicodedata` names), a colour that is CMYK, has an alpha,
  or is written as a Python expression, `inf` or `nan` as numbers, and in `<para>` the indents, justified lines, borders,
  text transforms, `autoLeading`, `wordWrap`, end dots and hyphenation.

### Fragments with their own style (t3pdf)

A fragment (`ParaFrag`) now carries its font, size, colour, rise, background, `nobr`, its underline and strike lines and its
links, and the layout measures each one at its own size (`paragraph.go`, `paraparser.go`):

- `u` and `strike` (and `a` with `underline`) add a line entry `(n, kind, colour, width, offset, rise, repeats, gap)`; the
  lines of a run are drawn after each text line (`paradraw.go`, `_do_post_text`: sorted by their number, with the font sizes of
  the first fragment and of the largest one in the offset and width expressions `0.5*f`, `-0.125*F`, `*L`) as canvas lines
  before the text object, with the stroke colour and line width compared to the canvas state (`canvasState` now tracks both);
  a one-fragment paragraph draws them with `_do_under_line`;
- `font`, `span` (without `style`, which raises), `nobr`, `sup`, `super`, `sub` change name, size, colour, background, rise
  (`Ts`) and the space-shrinkage and line-height inputs; `font name=` goes through reportlab's `ps2tt`, a `link name=` through
  `tt2ps` with its fall back to `ps2tt`; sizes and rises take `+n`, `n%` and units (`mm`, `in`, `pt`, `cm`, `pica`);
- `a href` and `link` write one `/Link` annotation per run of a link on a line, numbered when the paragraph is drawn (before
  the page that holds them, which lists them as `/Annots`), with the rectangle taken through the canvas matrix
  (`canvasState.matrix`); `pdf:` targets are `GoToR`;
- `img` with a base64 JPEG data URL is a word of its width laid out between text, drawn with `drawImage` at the baseline offset
  of its `valign` and followed by a move of the text cursor; percentages are of the paragraph width (width) and the font size
  (height, valign). Image names are numbered by the order of their objects in the parity standard (and the `/XObject` entries
  sorted by that number), since their order in the document follows the PIL pixel digest;
- `<para>` attributes change a copy of the style: size, font, colour, leading, spacing, left, centre and right alignment,
  background colour, the underline and strike defaults, `spaceShrinkage`, `splitLongWords`, `allowWidows`, `allowOrphans`.
  Attribute values go through reportlab's converters (`_num`, `_numpct`, `_bool`, `toColor`, `_CheckUS`), and every invalid one
  is an exception of the export.

Attribute values are those of CPython 3.13.16's `parse_starttag` (quotes removed, character references replaced with the
attribute rules: `html5_gen.go`, `htmlattr.go`).

One fragment is laid out as before (reportlab's kind 0), now with soft hyphens: a word is cut at its last soft hyphen that
fits, with a `-`. Several fragments (text around `&amp;` is three of them) take the kind 1 layout: `_getFragWords`, the line
breaking with the space shrinkage counted over the line's fragments, long words cut character by character
(`_splitFragWord`), soft hyphens (`_SHYWord.shyphenate`), and the drawing with one text run per fragment and the font
switches reportlab writes. A paragraph cut between two pages keeps the frag words of its lines: a long word cut by the page
is joined again and cut anew on the next one, in both layouts (`_split_blParaSimple`, `_split_blParaProcessed`), with the
effects reportlab has there (a `<br/>` at the cut starts the next page with an empty line; hyphens at the end of a rejoined
piece are dropped; a soft hyphen at the very end of a word fails the document).

The text object merges a move that directly follows another one (`PDFTextObject.moveCursor`), as in centred paragraphs of
several lines. Objects are numbered in the order reportlab creates them: a font or image first used on a later page comes
after the pages before it.

The logo is read as Django reads it: `base64.b64decode` without validation (characters outside the alphabet are skipped;
an unfinished group, or no data at all, gives the `[LOGO]` paragraph), and a four-component JPEG is embedded as `DeviceCMYK`
with the inverting `/Decode` array.

### Greek text, characters by code and the other standard faces (s3pdfcanvas)

`greek` draws its text in `Symbol`: each Latin-1 character is the Symbol glyph of that byte (`symbolGreek`, generated from
reportlab's symbol codec, so `a` is `α` and `à` is `◊`), and a character outside Latin-1 raises `KeyError` and fails the
export as in reportlab. `unichar code="…"` is the character of a Python integer literal (decimal, `0x`, `0o`, `0b`,
underscores, surrounding spaces); `0x41` is `A`, an invalid literal or a code beyond U+10FFFF is the parser's syntax error.
The Courier and Times families are the four faces each, with their reportlab width vectors in WinAnsiEncoding, their
ascents and descents, and the same Symbol/ZapfDingbats substitution as Helvetica, so `<font name="Times-Italic">`,
`<font face="Courier">` and `<para font="Times-Bold">` are measured and drawn like reportlab's. Numbers may be written with
the decimal digits of any script (`unicode.Nd`): `１２` and `١٢` are `12` in a size, a rise, an offset or a `<unichar>` code.
`<index>` and `<onDraw>` parse and then fail the build: these documents have no index or callback hook, so reportlab raises
`Missing index callback attribute '_indexAdd'` (or `Missing onDraw callback attribute '<name>'`) and the export is the
Django 500 page; the Go error carries the same text.

## Golden test

`TESTS/fixtures/generate_pdf_golden.py` builds the golden documents with the contract's own builders (reportlab) and stores
their decoded form in `BE_GO/internal/pdfexport/testdata/golden.json`; `go test ./internal/pdfexport` rebuilds each one in Go
and compares it byte for byte in that form (people, no rows, every value type, escapes and substitution fonts, several pages,
a page filled exactly, A3, A2, wider than A2, a row taller than the page, alignment, portrait; balance sheets without a logo,
with colour and grey JPEG logos, on several pages, with long, unbroken, empty and multi-line denominations, substitution
fonts in the header, and the closing line on a page of its own: 35 documents, 66 with the saved-report ones below, 106
with the markup ones: entities, `&` and `<`, tags, soft hyphens, long words and paragraphs cut over pages in both layouts,
logos in lenient base64 and CMYK, 14 of them raising, and 17 more in s3pdfcanvas for `<greek>`, `<unichar>`, the Courier and
Times faces, the index/onDraw failures and Greek text in headers, cells and dates: 201 documents, 48 raising), and checks the
digest of a document against reportlab's invariant one. `TESTS/fixtures/generate_pdf_paragraphs.py` writes
`testdata/paragraphs.json`: what reportlab's `Paragraph` makes of 3057 texts (457 authored, 2600 drawn from markup pieces with
a fixed seed), the fragments, an exception, or a document the build cannot draw; `TestParagraphParserCorpus` compares each
one. The same generator writes the tables of reportlab's own data (`entities_gen.go`, `colors_gen.go`, `html5_gen.go`,
`fonts_std_gen.go`). `refusal_test.go` pins the refused inputs.

## Evidence

Five families answer their `pdf` format in Go (receipts `<family>-http-v2pdf-*`): printing-generate 113/113,
subscription-reads 350/350, course-list 159/159, course-subscriptions 145/145 and balance-sheet 89/89 on native Django, native
Go, Caddy `/api/v1` and `/api/v2` (`v2pdf-versioned-r2`, after merging `codex/go-backend-parity`), each with a RED run on Go's
earlier refusal and a mutation control (see the family docs). `xlsxexport` gained `Value.PythonStr`; payment-export 87/87
and misc-exports 70/70 re-run natively (`v2pdf-rerun-r1`).

The paragraph ports of s3pdfcanvas are proven on the saved-report file tools (templates-reports): RED
`s3pdfcanvas-red-r2` (Django 246/246, Go 500 on `run-export-pdf-greek-unichar-fonts`), GREEN `s3pdfcanvas-versioned-r4`
(native Django, native Go, Caddy `/api/v1` and `/api/v2`, 246/246 each) and mutation `s3pdfcanvas-mutation-r4` (the greek
byte table read as identity) CAUGHT; the other PDF families re-ran green with the same label: balance-sheet 97/97,
printing-generate 113/113, course-list 175/175, course-subscriptions 168/168, subscription-reads 364/364.

## The saved-report documents

`pdfexport.ReportTable{Headers, Rows, Title, Footer, TextBefore, TextAfter}` is the saved-report `PDFExporter` and
`pdfexport.Attendance{Courses}` is `export_attendance_matrix_pdf` (templates-reports.md, "File tools"):

- a `Paragraph` in every cell and no column widths: `Table._calcPreliminaryWidths` (`widths.go`) starts every column from the
  widest word plus the paddings, shares spare width towards equal columns (the least disappointed first) and shrinks every
  column by the same ratio when the page is too narrow;
- `repeatRows=1`: the header row and its commands are repeated on every part of a split table (`ROWBACKGROUNDS` included);
- `Spacer` and `PageBreak` between the texts, the tables and the courses, 40 points of top margin;
- the attendance header: fixed widths and the `VerticalText` flowable (a 90 degree rotation multiplied into the translation
  reportlab already wrote, then the text).

Contract engines `report_table` and `attendance` (`TESTS/contracts/pdf.py`); 31 more golden documents (one, many and
squeezed columns, wrapped cells, several pages, A3, A2, a row taller than the page, the texts, texts cut over two and over
many pages, one and several courses, more dates than the page holds).
