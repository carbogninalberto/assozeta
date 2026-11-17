package pandasxlsx

import (
	"archive/zip"
	"bytes"
	"encoding/xml"
	"errors"
	"io"
	"math"
	"path"
	"strings"

	"github.com/dlclark/regexp2"
)

const (
	mainNS         = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
	relationNS     = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
	packageRelNS   = "http://schemas.openxmlformats.org/package/2006/relationships"
	contentTypesNS = "http://schemas.openxmlformats.org/package/2006/content-types"
	stringsContent = "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"
	stylesPart     = "xl/styles.xml"
	arcWorkbook    = "xl/workbook.xml"
)

// maxPartBytes bounds the parts openpyxl parses whole (see archive.read). It is
// a variable so a test can lower it.
var maxPartBytes = 256 << 20

var workbookTypes = map[string]bool{
	"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml":    true,
	"application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml": true,
	"application/vnd.ms-excel.sheet.macroEnabled.main+xml":                          true,
	"application/vnd.ms-excel.template.macroEnabled.main+xml":                       true,
}

// workbookTypeOrder is the order _find_workbook_part looks a workbook part up
// in.
var workbookTypeOrder = []string{
	"application/vnd.ms-excel.template.macroEnabled.main+xml",
	"application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml",
	"application/vnd.ms-excel.sheet.macroEnabled.main+xml",
	"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
}

// builtinFormats is openpyxl's BUILTIN_FORMATS.
var builtinFormats = map[int]string{0: "General", 1: "0", 2: "0.00", 3: "#,##0", 4: "#,##0.00", 5: `"$"#,##0_);("$"#,##0)`,
	6: `"$"#,##0_);[Red]("$"#,##0)`, 7: `"$"#,##0.00_);("$"#,##0.00)`, 8: `"$"#,##0.00_);[Red]("$"#,##0.00)`, 9: "0%", 10: "0.00%",
	11: "0.00E+00", 12: "# ?/?", 13: "# ??/??", 14: "mm-dd-yy", 15: "d-mmm-yy", 16: "d-mmm", 17: "mmm-yy", 18: "h:mm AM/PM",
	19: "h:mm:ss AM/PM", 20: "h:mm", 21: "h:mm:ss", 22: "m/d/yy h:mm", 37: "#,##0_);(#,##0)", 38: "#,##0_);[Red](#,##0)",
	39: "#,##0.00_);(#,##0.00)", 40: "#,##0.00_);[Red](#,##0.00)", 41: `_(* #,##0_);_(* \(#,##0\);_(* "-"_);_(@_)`,
	42: `_("$"* #,##0_);_("$"* \(#,##0\);_("$"* "-"_);_(@_)`, 43: `_(* #,##0.00_);_(* \(#,##0.00\);_(* "-"??_);_(@_)`,
	44: `_("$"* #,##0.00_)_("$"* \(#,##0.00\)_("$"* "-"??_)_(@_)`, 45: "mm:ss", 46: "[h]:mm:ss", 47: "mmss.0", 48: "##0.0E+0", 49: "@"}

var (
	stripFormat     = regexp2.MustCompile(`".*?"|\[(?!hh?\]|mm?\]|ss?\])[^\]]*\]`, regexp2.None)
	dateFormat      = regexp2.MustCompile(`(?<![_\\])[dmhysDMHYS]`, regexp2.None)
	timedeltaFormat = regexp2.MustCompile(`\[hh?\](:mm(:ss(\.0*)?)?)?|\[mm?\](:ss(\.0*)?)?|\[ss?\](\.0*)?`, regexp2.IgnoreCase)
)

func matches(pattern *regexp2.Regexp, text string) bool {
	ok, err := pattern.MatchString(text)
	return err == nil && ok
}

// isDateFormat is openpyxl's is_date_format.
func isDateFormat(format string) bool {
	first, _, _ := strings.Cut(format, ";")
	stripped, err := stripFormat.Replace(first, "", -1, -1)
	return err == nil && matches(dateFormat, stripped)
}

func isTimedeltaFormat(format string) bool {
	first, _, _ := strings.Cut(format, ";")
	return matches(timedeltaFormat, first)
}

type archive map[string]*zip.File

// open is the part named as openpyxl's archive.open hands it to iterparse: a
// reader of its decompressed bytes, with no size bound (a worksheet is read as
// it is parsed: openpyxl's iterparse clears each element, so its size alone is
// no memory question). A part the archive does not hold raises there
// (ErrInvalid).
func (a archive) open(name string) (io.ReadCloser, error) {
	file, ok := a[name]
	if !ok {
		return nil, ErrInvalid
	}
	reader, err := file.Open()
	if err != nil {
		return nil, ErrInvalid
	}
	return reader, nil
}

// read is the part named as fromstring reads it: whole, and bound to
// maxPartBytes. openpyxl parses the workbook, its relationships, the content
// types, the shared strings and the stylesheet with fromstring, so a part past
// the bound needs that much memory on top of the rest of the request. The host
// cannot parse such a part, which makes openpyxl raise MemoryError: the view
// then answers what it answers for every read_excel failure, the 500 page of
// its xlrd retry (ImportError, xlrd not being installed, or XLRDError for an
// xlsx file where it is) (owner decision of 2026-11-16, "closest Django
// answer": the read fails, so ErrInvalid). A part the archive does not hold and
// a corrupt entry raise in pandas too (ErrInvalid).
func (a archive) read(name string) ([]byte, error) {
	reader, err := a.open(name)
	if err != nil {
		return nil, err
	}
	file := a[name]
	if file.UncompressedSize64 > uint64(maxPartBytes) {
		reader.Close()
		return nil, ErrInvalid
	}
	data, err := io.ReadAll(io.LimitReader(reader, int64(maxPartBytes)+1))
	reader.Close()
	if err != nil {
		return nil, ErrInvalid
	}
	if len(data) > maxPartBytes {
		return nil, ErrInvalid
	}
	return data, nil
}

// decode is openpyxl's from_tree: a part is parsed as expat parses it (see
// encoding.go for the declaration's encoding), and malformed XML raises there
// (ErrInvalid).
func decode(data []byte, target any) error {
	content, err := xmlContent(bytes.NewReader(data))
	if err != nil {
		return err
	}
	if err = xml.NewDecoder(content).Decode(target); err != nil {
		return ErrInvalid
	}
	return nil
}

type relationships struct {
	XMLName xml.Name `xml:"http://schemas.openxmlformats.org/package/2006/relationships Relationships"`
	Items   []struct {
		ID     string `xml:"Id,attr"`
		Type   string `xml:"Type,attr"`
		Target string `xml:"Target,attr"`
		Mode   string `xml:"TargetMode,attr"`
	} `xml:"http://schemas.openxmlformats.org/package/2006/relationships Relationship"`
}

type text struct {
	Plain *string `xml:"http://schemas.openxmlformats.org/spreadsheetml/2006/main t"`
	Runs  []struct {
		Text *string `xml:"http://schemas.openxmlformats.org/spreadsheetml/2006/main t"`
	} `xml:"http://schemas.openxmlformats.org/spreadsheetml/2006/main r"`
}

// content is openpyxl's Text.content.
func (t text) content() string {
	var builder strings.Builder
	if t.Plain != nil {
		builder.WriteString(*t.Plain)
	}
	for _, run := range t.Runs {
		if run.Text != nil {
			builder.WriteString(*run.Text)
		}
	}
	return builder.String()
}

func partName(base, target string) string {
	if strings.HasPrefix(target, "/") {
		return strings.TrimPrefix(target, "/")
	}
	return path.Join(path.Dir(base), target)
}

// partPath is openpyxl's PartName[1:]: a part name without its first
// character ('/xl/workbook.xml' is 'xl/workbook.xml').
func partPath(name string) string {
	runes := []rune(name)
	if len(runes) < 2 {
		return ""
	}
	return string(runes[1:])
}

// ReadAll is pandas.read_excel(io.BytesIO(data), engine="openpyxl",
// converters=...) with the view's text converter on the columns textual names
// (nil: no converters): a number of such a column is its str(), every other
// cell stays as it is, and the column gets no numeric or boolean reading. A
// worksheet with a row numbered past maxRowNumber stops the read with
// ErrRowsPastBound before that row is held.
func ReadAll(data []byte, textual func(column string) bool) (*Frame, error) {
	sheet, context, err := openSheet(data)
	if err != nil {
		return nil, err
	}
	rows, err := sheetRows(sheet, context)
	sheet.Close()
	if err != nil {
		return nil, err
	}
	return build(rows, textual)
}

// ReadColumns is ReadAll for the columns alone, which do not depend on the
// rows: the labels the first row and the frame's width make (a wider row pads
// the header with empty names), with no row held. It streams every row, so a
// worksheet of any size answers, and a cell openpyxl raises on still raises:
// the cell conversions and the row numbers are the same as ReadAll's, only the
// per-column type inference of the values (which needs every row) is left out.
func ReadColumns(data []byte) (*Frame, error) {
	sheet, context, err := openSheet(data)
	if err != nil {
		return nil, err
	}
	header, width, err := sheetColumns(sheet, context)
	sheet.Close()
	if err != nil {
		return nil, err
	}
	return buildColumns(header, width), nil
}

// openSheet is read_excel's walk to the first worksheet, left open for the
// caller to read: the workbook part from the content types, its
// relationships, the sheet openpyxl keeps, the shared strings and the
// stylesheet.
func openSheet(data []byte) (io.ReadCloser, sheetContext, error) {
	var context sheetContext
	reader, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return nil, context, ErrInvalid
	}
	files := archive{}
	for _, file := range reader.File {
		// zipfile opens the last member of a name an archive repeats.
		files[file.Name] = file
	}
	if _, ok := files["[Content_Types].xml"]; !ok {
		return nil, context, ErrInvalid
	}
	raw, err := files.read("[Content_Types].xml")
	if err != nil {
		return nil, context, err
	}
	var types struct {
		XMLName  xml.Name `xml:"http://schemas.openxmlformats.org/package/2006/content-types Types"`
		Defaults []struct {
			Type string `xml:"ContentType,attr"`
		} `xml:"http://schemas.openxmlformats.org/package/2006/content-types Default"`
		Overrides []struct {
			Part string `xml:"PartName,attr"`
			Type string `xml:"ContentType,attr"`
		} `xml:"http://schemas.openxmlformats.org/package/2006/content-types Override"`
	}
	if err = decode(raw, &types); err != nil {
		return nil, context, err
	}
	// openpyxl looks for the workbook by content type (a macro-enabled one
	// first, then a template one, then a plain one), and then for a Default of
	// a workbook type, which is 'xl/workbook.xml'; the shared strings are the
	// first part of their content type. A part name is read from its second
	// character ('/xl/workbook.xml' is 'xl/workbook.xml').
	parts := map[string]string{}
	seen := map[string]bool{}
	var defaults []string
	stringsPart, hasStrings := "", false
	for _, override := range types.Overrides {
		if workbookTypes[override.Type] && !seen[override.Type] {
			seen[override.Type] = true
			parts[override.Type] = override.Part
		}
		if override.Type == stringsContent && !hasStrings {
			stringsPart, hasStrings = partPath(override.Part), true
		}
	}
	for _, item := range types.Defaults {
		defaults = append(defaults, item.Type)
	}
	workbookPart, found := "", false
	for _, kind := range workbookTypeOrder {
		if part, ok := parts[kind]; ok {
			workbookPart, found = partPath(part), true
			break
		}
	}
	if !found {
		for _, kind := range defaults {
			if workbookTypes[kind] {
				workbookPart, found = partPath("/"+arcWorkbook), true
				break
			}
		}
	}
	if !found {
		return nil, context, ErrInvalid // 'File contains no valid workbook part'
	}
	if raw, err = files.read(workbookPart); err != nil {
		return nil, context, err
	}
	var workbook struct {
		XMLName    xml.Name `xml:"http://schemas.openxmlformats.org/spreadsheetml/2006/main workbook"`
		Properties struct {
			Date1904 string `xml:"date1904,attr"`
		} `xml:"http://schemas.openxmlformats.org/spreadsheetml/2006/main workbookPr"`
		Sheets []struct {
			ID string `xml:"http://schemas.openxmlformats.org/officeDocument/2006/relationships id,attr"`
		} `xml:"sheets>sheet"`
	}
	if err = decode(raw, &workbook); err != nil {
		return nil, context, err
	}
	// openpyxl's Bool: every text but '', 'false', 'f' and '0' is true.
	mac := false
	switch workbook.Properties.Date1904 {
	case "", "false", "f", "0":
	default:
		mac = true
	}
	if raw, err = files.read(path.Join(path.Dir(workbookPart), "_rels", path.Base(workbookPart)+".rels")); err != nil {
		return nil, context, err
	}
	var rels relationships
	if err = decode(raw, &rels); err != nil {
		return nil, context, err
	}
	targets := map[string]string{}
	kinds := map[string]string{}
	for _, item := range rels.Items {
		// get_dependents resolves a target against the workbook's folder, but an
		// external one keeps its Target; the last relationship of an id wins.
		target := item.Target
		if item.Mode != "External" {
			target = partName(workbookPart, item.Target)
		}
		targets[item.ID], kinds[item.ID] = target, item.Type
	}
	// read_excel's sheet 0: the first worksheet openpyxl keeps. A sheet without
	// a relationship id and one whose part the archive does not hold are
	// skipped, a chartsheet is no worksheet, and a relationship id the workbook
	// does not list raises.
	sheetPart := ""
	for _, sheet := range workbook.Sheets {
		if sheet.ID == "" {
			continue
		}
		target, ok := targets[sheet.ID]
		if !ok {
			return nil, context, ErrInvalid
		}
		if _, present := files[target]; !present {
			continue
		}
		if strings.Contains(kinds[sheet.ID], "chartsheet") {
			// read_chartsheet wants the chartsheet's own relationships part.
			if _, present := files[path.Join(path.Dir(target), "_rels", path.Base(target)+".rels")]; !present {
				return nil, context, ErrInvalid
			}
			continue
		}
		sheetPart = target
		break
	}
	if sheetPart == "" {
		return nil, context, ErrInvalid // 'Worksheet index 0 is invalid, 0 worksheets found'
	}
	var shared []string
	if hasStrings {
		if raw, err = files.read(stringsPart); err != nil {
			return nil, context, err
		}
		var table struct {
			XMLName xml.Name `xml:"http://schemas.openxmlformats.org/spreadsheetml/2006/main sst"`
			Items   []text   `xml:"http://schemas.openxmlformats.org/spreadsheetml/2006/main si"`
		}
		if err = decode(raw, &table); err != nil {
			return nil, context, err
		}
		for _, item := range table.Items {
			// read_string_table: the escape of an underscore is dropped; other
			// '_xHHHH_' sequences stay as written.
			shared = append(shared, strings.ReplaceAll(item.content(), "x005F_", ""))
		}
	}
	// The stylesheet is 'xl/styles.xml' whatever the relationships say; a
	// workbook without it has no date formats.
	var styles struct {
		XMLName xml.Name `xml:"http://schemas.openxmlformats.org/spreadsheetml/2006/main styleSheet"`
		Formats []struct {
			ID   string `xml:"numFmtId,attr"`
			Code string `xml:"formatCode,attr"`
		} `xml:"numFmts>numFmt"`
		Cells []struct {
			Format *string `xml:"numFmtId,attr"`
		} `xml:"cellXfs>xf"`
	}
	if _, present := files[stylesPart]; present {
		if raw, err = files.read(stylesPart); err != nil {
			return nil, context, err
		}
		if err = decode(raw, &styles); err != nil {
			return nil, context, err
		}
	}
	custom := map[int64]string{}
	for _, format := range styles.Formats {
		id, ok := pythonInt(format.ID)
		if !ok || !id.IsInt64() {
			return nil, context, ErrInvalid // openpyxl's Integer: int(text) raises
		}
		custom[id.Int64()] = format.Code
	}
	// 0 number, 1 date, 2 timedelta (a date format that is also a timedelta
	// format).
	styleKinds := make([]int, len(styles.Cells))
	for index, style := range styles.Cells {
		id := int64(0) // a missing numFmtId is General
		if style.Format != nil {
			index, ok := pythonInt(*style.Format)
			if !ok || !index.IsInt64() {
				return nil, context, ErrInvalid // openpyxl's Integer: int(text) raises
			}
			id = index.Int64()
		}
		format, known := custom[id]
		if !known {
			format, known = builtinFormats[int(id)]
		}
		if known && isDateFormat(format) {
			styleKinds[index] = 1
			if isTimedeltaFormat(format) {
				styleKinds[index] = 2
			}
		}
	}
	sheet, err := files.open(sheetPart)
	if err != nil {
		return nil, context, err
	}
	context = sheetContext{shared: shared, styleKinds: styleKinds, mac: mac}
	return sheet, context, nil
}

// sheetContext is what a cell's value depends on beyond the cell itself.
type sheetContext struct {
	shared     []string
	styleKinds []int
	mac        bool // the 1904 date system
}

type sheetCell struct {
	XMLName   xml.Name
	Reference string   `xml:"r,attr"`
	Type      *string  `xml:"t,attr"`
	Style     *string  `xml:"s,attr"`
	Values    []string `xml:"http://schemas.openxmlformats.org/spreadsheetml/2006/main v"`
	Inline    []text   `xml:"http://schemas.openxmlformats.org/spreadsheetml/2006/main is"`
}

type sheetRow struct {
	Number *string     `xml:"r,attr"`
	Cells  []sheetCell `xml:",any"`
}

// maxRowNumber is the last row of a frame this package builds. openpyxl's
// read-only reader pads every skipped row (ReadOnlyWorksheet._cells_by_row
// yields an empty row per number between the rows it has), so the DataFrame
// holds one row per number: measured with pandas 2.2.3, a workbook whose only
// row is numbered 1048577 answers a frame of 1048576 rows (322 MB resident),
// and 10000000 a frame of 9999999 rows (1.66 GB). ReadAll therefore stops at
// ErrRowsPastBound, and the caller answers as the view answers when the read
// runs out of memory (the owner's decision of 2026-11-16); ReadColumns streams
// the rows, so the columns need no memory for them. 1048576 is also the last
// row Excel writes.
const maxRowNumber = 1048576

// rowNumber is openpyxl's parse_row row number: int(text), or float(text) when
// int raises and the float is whole; a number outside int64 saturates (it is
// past the frame bound either way). A number before row 1 leaves the row where
// it is (the reader skips it).
func rowNumber(text string) (int64, error) {
	if number, ok := pythonInt(text); ok {
		switch {
		case !number.IsInt64() && number.Sign() > 0:
			return math.MaxInt64, nil
		case !number.IsInt64():
			return math.MinInt64, nil
		}
		return number.Int64(), nil
	}
	value, ok := pythonFloat(text)
	if !ok || math.IsNaN(value) || math.IsInf(value, 0) || value != math.Trunc(value) {
		return 0, ErrInvalid
	}
	switch {
	case value >= math.MaxInt64:
		return math.MaxInt64, nil
	case value <= math.MinInt64:
		return math.MinInt64, nil
	}
	return int64(value), nil
}

// scanRows is ReadOnlyWorksheet.rows after reset_dimensions(): the rows the
// worksheet's XML holds (a row is any element of the main namespace named
// 'row', wherever it sits), in document order, from row 1, with the cells from
// column 1 to each row's last cell. visit receives each row's number and cells.
func scanRows(part io.Reader, context sheetContext, visit func(number int64, cells []cell) error) error {
	content, err := xmlContent(part)
	if err != nil {
		return err
	}
	decoder := xml.NewDecoder(content)
	rowCounter, root := int64(0), false
	for {
		token, err := decoder.Token()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return ErrInvalid
		}
		element, ok := token.(xml.StartElement)
		if !ok {
			continue
		}
		root = true
		if element.Name.Space != mainNS || element.Name.Local != "row" {
			continue
		}
		var row sheetRow
		if err = decoder.DecodeElement(&row, &element); err != nil {
			return ErrInvalid
		}
		if row.Number != nil {
			number, err := rowNumber(*row.Number)
			if err != nil {
				return err
			}
			rowCounter = number
		} else if rowCounter < math.MaxInt64 {
			rowCounter++
		}
		cells, err := rowCells(row, context)
		if err != nil {
			return err
		}
		if err = visit(rowCounter, cells); err != nil {
			return err
		}
	}
	if !root {
		return ErrInvalid // no element at all: ElementTree's ParseError
	}
	return nil
}

// sheetRows is build's rows: one per row number from 1 to the last one read, a
// row at or before an earlier one skipped. A row past maxRowNumber stops the
// read with ErrRowsPastBound before the empty rows below it are held.
func sheetRows(part io.Reader, context sheetContext) ([][]cell, error) {
	var rows [][]cell
	err := scanRows(part, context, func(number int64, cells []cell) error {
		if number > maxRowNumber {
			return ErrRowsPastBound
		}
		if number <= int64(len(rows)) {
			return nil // a row at or before the last one read is skipped
		}
		for int64(len(rows)) < number-1 {
			rows = append(rows, nil)
		}
		rows = append(rows, cells)
		return nil
	})
	if err != nil {
		return nil, err
	}
	return rows, nil
}

// sheetColumns is ReadColumns' walk: the cells of row 1 and the width of the
// widest row (after the trailing blank cells are trimmed), with no rows kept.
// Every row is still parsed and converted, so a cell openpyxl raises on raises
// here too.
func sheetColumns(part io.Reader, context sheetContext) ([]cell, int, error) {
	var header []cell
	width, count := 0, int64(0)
	err := scanRows(part, context, func(number int64, cells []cell) error {
		if number <= count {
			return nil // a row at or before the last one read is skipped
		}
		for len(cells) > 0 && cells[len(cells)-1].blank() {
			cells = cells[:len(cells)-1]
		}
		if number == 1 {
			header = cells
		}
		if len(cells) > width {
			width = len(cells)
		}
		count = number
		return nil
	})
	if err != nil {
		return nil, 0, err
	}
	return header, width, nil
}
