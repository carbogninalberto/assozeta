# XLSX exports — a part-by-part parity standard and a Go writer

Django builds spreadsheets with three libraries (pandas 2.2.3, openpyxl 3.1.5 without lxml, XlsxWriter 3.2.2):

| Code path | Library | Views |
| --- | --- | --- |
| `get_excel_base64` (`application/utils/excel_utils.py`) | pandas + openpyxl | `payment/list/export` `m=xlsx`, `invoice/list/export` `m=xlsx`, `printing/generate` (excel format) |
| `_export_to_excel` (`subscriptions_views.py`) | pandas + xlsxwriter, index column | `subscription/list/export` `filetype` containing `xls` |
| `modules_response_export` (inline) | pandas + xlsxwriter, sheet `Risposte` | `modules/<module_id>/export` |
| `generate_xlsx_report_from_data` (`instructors_utils.py`) | pandas + xlsxwriter | `instructor/report` |
| `export_all_data` (inline, `profile_views.py`) | plain openpyxl | `export-all-data` |

`BE_GO/internal/xlsxexport` reproduces all three writers. Payment, invoice, subscription, module and instructor exports now
run in Go (see [payment-export](payment-export.md), [misc-exports](misc-exports.md), [modules-responses](modules-responses.md),
[instructor-writes](instructor-writes.md)).

## The parity standard

A workbook is never byte-reproducible as a whole: `docProps/core.xml` holds the creation and modification times and every zip
entry holds a local time stamp. Parity is checked part by part:

- the same zip entry names in the same order;
- per entry the same compression method and general purpose flags (Python's `zipfile`: deflate, flags 0, no data descriptor);
- byte-identical uncompressed parts, except the `dcterms:created` and `dcterms:modified` values of `docProps/core.xml`, which
  must fall within the request window (start truncated to the second);
- zip time stamps and the compressed bytes themselves are not compared.

The Go writer also matches the entries' external attributes and versions (checked by the golden test). Opt-in for contracts:
`TESTS/contracts/xlsx.py` (`{'$xlsx': spec}` names the workbook by the inputs the view hands its library; the helper builds the
expected workbook with the same libraries and calls; `resolve(expected, actual, started, finished)` replaces a matching
workbook by the received value). Go: `xlsxexport.Equivalent(expected, actual, from, to)`.

## The writer

- `PandasOpenpyxl(data, columns, now)` is `get_excel_base64`: `zip` truncation to the shortest column, the DataFrame's dtype
  inference, `pd.to_numeric(..., errors='coerce')` on the non-null values (pandas' `maybe_convert_numeric` and
  `precise_xstrtod`, including the spelled infinities and `int()` of integer-looking text), `Int64` for all-integer columns
  (a float column when the cast fails), widths `min(max(len(str(value)), len(column)) + 2, 50)` for columns A..Z (`""` for an
  empty frame, where `max` is NaN; a 27th column is Django's `None`), and openpyxl's cells: inline strings, `%.16g` numbers,
  `inf`/`-inf` text, `=...` formulas, error codes, the 32767-character cut and `IllegalCharacterError` for control characters.
- `PandasXlsxwriter(Frame, now)` is `df.to_excel` through xlsxwriter plus `set_column`: shared strings in pandas' write order
  (header, index, then the body column by column), `%.16G` numbers, booleans, blanks skipped, `_xHHHH_` escapes, merged
  `<col>` ranges with xlsxwriter's width conversion, row spans, external hyperlinks (`http(s)://`, `ftp(s)://`, `mailto:`) with
  their styles and relationships, and the styles.xml for the formats in use.
- `OpenpyxlRows(titles, sheets, now)` is a plain openpyxl workbook whose default sheet is removed.
- `StrLength(values)` is `astype(str).str.len().max()` after dtype inference.

Inputs Go refuses (`ErrNotPorted`; the callers answer the 500 page before any write):

- numeric-looking text with 17 or more significant digits beyond 2**53: pandas accumulates digits with `number * 10 + digit`,
  which the compiler contracts into a fused multiply-add on arm64 (the macOS and aarch64 wheels) and not on x86-64, so the
  parsed value depends on the platform Django runs on;
- xlsxwriter formulas (`=...`, `{=...}`), rich-string text (`<r>...</r>`), `internal:`/`external:` links and URLs with `#`;
- integers beyond int64, booleans in a column `get_excel_base64` coerces, duplicate column names in `get_excel_base64`.

## Golden test

`TESTS/fixtures/generate_xlsx_golden.py` builds 115 workbooks with Django's own functions (extracted from the sources with
`ast`; the two inline view workbooks are copied line for line) and stores their zip entries and parts in
`BE_GO/internal/xlsxexport/testdata/golden.json`. `go test ./internal/xlsxexport` rebuilds each one in Go and compares it part
by part; Django's failures (`None` or a raise) must be `ErrDjango`, the listed refusals `ErrNotPorted`. The cases cover the
payment shapes, numeric coercion edge cases, 40 seeded random columns, escaping, whitespace, Unicode, formulas, error codes,
hyperlinks, long strings, empty frames and multi-sheet openpyxl workbooks. Dropping the `Int64` conversion fails 11 of them.

## Not ported

- `printing/generate`'s `pdf` format (in-process reportlab); its `excel` format runs in Go
  ([printing-generate](printing-generate.md)). `export-all-data` runs in Go ([export-all-data](export-all-data.md)):
  `wb.save(HttpResponse)` streams the zip (flag `0x08`, data descriptors), written by `OpenpyxlRowsResponse` and built by
  the contract helper with `'stream': True`.
- `instructor/report` dates other than `D/M/YYYY` with `/`, `-` or `.` (dateutil accepts many more spellings; an ISO date is
  read day first).

## Evidence

RED labels `xlsx-red-r1` (Django passes every authored case; Go fails the first new one), native GREEN `xlsx-native-r*`,
four-transport GREEN `xlsx-versioned-r1` and mutations per family:

| Family | Cases | Mutation (CAUGHT) |
| --- | --- | --- |
| payment-export | 87 | `xlsx-mutation-r2`: '' instead of '-' for a payment without an account fails `payment-export-xlsx` |
| misc-exports | 70 | `xlsx-mutation-r2`: `len(col) + 2` widths fail `misc-exports-subs-xls-full` |
| modules-responses | 135 | `xlsx-mutation-r1`: joining attachments with ', ' fails `modules-responses-export-owner` |
| instructor-writes | 150 | `xlsx-mutation-r1`: UTC instead of Europe/Rome dates fail `instructor-writes-report-owner` |

`payment-export`'s `xlsx-mutation-r1` (no `Int64` conversion) was not caught by the family (its integer columns have long
headers); the golden test catches it.

## Saved-report workbooks

`xlsxexport.ReportWorkbook` / `ReportSheetsWorkbook` are `XLSXExporter.export` / `export_multi_sheet` after
`format_export_data` (pandas + openpyxl: the numeric conversion of `get_excel_base64`, columns holding a list or dict left as
they are, widths for any number of columns, sheet names cut and numbered), and `xlsxexport.AttendanceWorkbook` is
`export_attendance_matrix_xlsx` in its three layouts (fixed widths, the rotated alignment as a third cell style on the first
row of as many columns as there are dates: a repeated date label moves it onto the `Presenze` header and onto empty cells
past the data, as openpyxl writes them). openpyxl's sheet-title rules apply: an invalid or empty title and one that differs
from an earlier title only by case fail as in Django. Contract engines `report_sheets` and `attendance`
(`TESTS/contracts/xlsx.py`); the golden generator builds 40 more workbooks with Django's own code and checks that the
contract's builders give the same parts. Evidence: templates-reports 125/125 on four transports (`v2rep-versioned-r2`).

v2-reports-2: a frame without columns writes no cell (a workbook without any header cell keeps openpyxl's plain styles); a
numbered sheet name equal to an earlier one writes the later frame over the earlier sheet (cells and widths in place; an
alignment-only cell keeps the earlier header's text); a course without a title is the sheet `Foglio` and an empty `Corso`
cell measured as `None`, and fails as in Django for its own workbook; labels that are not text keep their type in the
header row (`ReportSheet.Labels`, `LabelTexts`). 14 more golden workbooks (`rs-collision` is now compared). Evidence:
templates-reports 174/174 on four transports (`v2rep2-versioned-r2`).

v2-reports-3: labels that compare equal in Python (`1`, `1.0`, `true`) name the same columns and are neither converted nor sized apart
(widths follow the number of rows); an integer label beyond 64 bits is a number in the header row (`BigInt` value, object
index; the unsigned range and a `None` before the integers read as pandas does). 21 more golden workbooks. The MCP transport
now builds the same workbooks (mcp-server.md). Evidence: templates-reports 182/182 on four transports (`v2rep3-b-versioned-r1`).
