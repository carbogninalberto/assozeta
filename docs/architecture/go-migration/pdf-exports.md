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
  fewer than two lines fit;
- `platypus.Image` of a JPEG: the file is embedded as it is (ASCII85 + DCT) with its size and colour space, drawn through
  a form object listed in the resources of the page that uses it. reportlab names the object `FormXob.<md5 of the RGB
  pixels PIL decodes>`; another JPEG decoder does not give the same pixels bit for bit, so Go names it after the file's
  digest and the standard numbers image names by first appearance on both sides. Everything else about the image (bytes,
  dictionary, placement) is compared exactly.

Refused (`ErrNotPorted`, the callers answer the 500 page before any write): paragraph text containing `&`, `<` or a soft
hyphen (reportlab's markup parser and its multi-fragment layout are not ported), a paragraph that would be cut between two
pages, and a logo that is not an 8-bit grey or colour JPEG in strict base64.

A document reportlab fails to build is `ErrLayout`, which each caller answers as Django answers an exception of the export:
a row taller than a page (`LayoutError`), a list value in a cell (reportlab takes it for a list of flowables), rows of
another length than the columns, an invalid alignment.

## Golden test

`TESTS/fixtures/generate_pdf_golden.py` builds the golden documents with the contract's own builders (reportlab) and stores
their decoded form in `BE_GO/internal/pdfexport/testdata/golden.json`; `go test ./internal/pdfexport` rebuilds each one in Go
and compares it byte for byte in that form (people, no rows, every value type, escapes and substitution fonts, several pages,
a page filled exactly, A3, A2, wider than A2, a row taller than the page, alignment, portrait; balance sheets without a logo,
with colour and grey JPEG logos, on several pages, with long, unbroken, empty and multi-line denominations, substitution
fonts in the header, and the closing line on a page of its own: 35 documents), and checks the digest of a document against
reportlab's invariant one. `refusal_test.go` pins the refused inputs.

## Not ported: the saved-report files

`saved-reports/<pk>/run` with the tools `export_data`, `export_multi_sheet` and `get_attendance_matrix` stays refused
(`dto/report_tools.go`). Their PDFs put a `Paragraph` in every table cell and let reportlab size the columns
(`Table._calcPreliminaryWidths`), repeat the header row, and for the attendance matrix draw rotated date headers with a
custom flowable; their workbooks are styled openpyxl sheets and the CSV is the csv module's. None of that is in
`pdfexport` or `xlsxexport` yet.
