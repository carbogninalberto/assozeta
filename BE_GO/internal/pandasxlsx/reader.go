package pandasxlsx

import (
	"archive/zip"
	"bytes"
	"encoding/xml"
	"errors"
	"io"
	"path"
	"strconv"
	"strings"

	"github.com/dlclark/regexp2"
)

const (
	mainNS         = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
	relationNS     = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
	packageRelNS   = "http://schemas.openxmlformats.org/package/2006/relationships"
	contentTypesNS = "http://schemas.openxmlformats.org/package/2006/content-types"
	worksheetType  = relationNS + "/worksheet"
	stringsContent = "application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"
	stylesPart     = "xl/styles.xml"
	maxPartBytes   = 256 << 20
)

var workbookTypes = map[string]bool{
	"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml":    true,
	"application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml": true,
	"application/vnd.ms-excel.sheet.macroEnabled.main+xml":                          true,
	"application/vnd.ms-excel.template.macroEnabled.main+xml":                       true,
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

func (a archive) read(name string) ([]byte, error) {
	file, ok := a[name]
	if !ok || file.UncompressedSize64 > maxPartBytes {
		return nil, ErrUnsupported
	}
	reader, err := file.Open()
	if err != nil {
		return nil, ErrUnsupported
	}
	defer reader.Close()
	data, err := io.ReadAll(io.LimitReader(reader, maxPartBytes+1))
	if err != nil || len(data) > maxPartBytes {
		return nil, ErrUnsupported
	}
	return data, nil
}

func decode(data []byte, target any) error {
	decoder := xml.NewDecoder(bytes.NewReader(data))
	decoder.CharsetReader = func(string, io.Reader) (io.Reader, error) { return nil, ErrUnsupported }
	if err := decoder.Decode(target); err != nil {
		return ErrUnsupported
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

// ReadAll is pandas.read_excel(io.BytesIO(data), engine="openpyxl",
// converters=...) with the view's text converter on the columns textual names
// (nil: no converters): a number of such a column is its str(), every other
// cell stays as it is, and the column gets no numeric or boolean reading.
func ReadAll(data []byte, textual func(column string) bool) (*Frame, error) {
	reader, err := zip.NewReader(bytes.NewReader(data), int64(len(data)))
	if err != nil {
		return nil, ErrInvalid
	}
	files := archive{}
	for _, file := range reader.File {
		if _, duplicate := files[file.Name]; duplicate {
			return nil, ErrUnsupported
		}
		files[file.Name] = file
	}
	if _, ok := files["[Content_Types].xml"]; !ok {
		return nil, ErrInvalid
	}
	raw, err := files.read("[Content_Types].xml")
	if err != nil {
		return nil, err
	}
	var types struct {
		XMLName   xml.Name `xml:"http://schemas.openxmlformats.org/package/2006/content-types Types"`
		Overrides []struct {
			Part string `xml:"PartName,attr"`
			Type string `xml:"ContentType,attr"`
		} `xml:"http://schemas.openxmlformats.org/package/2006/content-types Override"`
	}
	if err = decode(raw, &types); err != nil {
		return nil, err
	}
	// The workbook and the shared strings are the first parts of their
	// content types.
	workbookPart, stringsPart, hasStrings := "", "", false
	for _, override := range types.Overrides {
		if workbookTypes[override.Type] && workbookPart == "" {
			workbookPart = strings.TrimPrefix(override.Part, "/")
		}
		if override.Type == stringsContent && !hasStrings {
			if !strings.HasPrefix(override.Part, "/") {
				return nil, ErrUnsupported
			}
			stringsPart, hasStrings = override.Part[1:], true
		}
	}
	if workbookPart == "" {
		return nil, ErrUnsupported
	}
	if raw, err = files.read(workbookPart); err != nil {
		return nil, err
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
		return nil, err
	}
	// openpyxl's Bool: every text but '', 'false', 'f' and '0' is true.
	mac := false
	switch workbook.Properties.Date1904 {
	case "", "false", "f", "0":
	default:
		mac = true
	}
	if raw, err = files.read(path.Join(path.Dir(workbookPart), "_rels", path.Base(workbookPart)+".rels")); err != nil {
		return nil, err
	}
	var rels relationships
	if err = decode(raw, &rels); err != nil {
		return nil, err
	}
	targets := map[string]string{}
	kinds := map[string]string{}
	for _, item := range rels.Items {
		if item.Mode != "" || item.ID == "" {
			return nil, ErrUnsupported
		}
		if _, duplicate := targets[item.ID]; duplicate {
			return nil, ErrUnsupported
		}
		targets[item.ID], kinds[item.ID] = partName(workbookPart, item.Target), item.Type
	}
	if len(workbook.Sheets) == 0 {
		return nil, ErrUnsupported
	}
	// read_excel's sheet 0: the first worksheet (chartsheets are not worksheets).
	first := workbook.Sheets[0].ID
	if kinds[first] != worksheetType {
		return nil, ErrUnsupported
	}
	var shared []string
	if hasStrings {
		if raw, err = files.read(stringsPart); err != nil {
			return nil, err
		}
		var table struct {
			XMLName xml.Name `xml:"http://schemas.openxmlformats.org/spreadsheetml/2006/main sst"`
			Items   []text   `xml:"http://schemas.openxmlformats.org/spreadsheetml/2006/main si"`
		}
		if err = decode(raw, &table); err != nil {
			return nil, err
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
			Format string `xml:"numFmtId,attr"`
		} `xml:"cellXfs>xf"`
	}
	if _, present := files[stylesPart]; present {
		if raw, err = files.read(stylesPart); err != nil {
			return nil, err
		}
		if err = decode(raw, &styles); err != nil {
			return nil, err
		}
	}
	custom := map[int]string{}
	for _, format := range styles.Formats {
		id, err := strconv.Atoi(format.ID)
		if err != nil {
			return nil, ErrUnsupported
		}
		custom[id] = format.Code
	}
	// 0 number, 1 date, 2 timedelta (a date format that is also a timedelta
	// format).
	styleKinds := make([]int, len(styles.Cells))
	for index, style := range styles.Cells {
		id := 0
		if style.Format != "" {
			if id, err = strconv.Atoi(style.Format); err != nil {
				return nil, ErrUnsupported
			}
		}
		format, known := custom[id]
		if !known {
			format, known = builtinFormats[id]
		}
		if known && isDateFormat(format) {
			styleKinds[index] = 1
			if isTimedeltaFormat(format) {
				styleKinds[index] = 2
			}
		}
	}
	if raw, err = files.read(targets[first]); err != nil {
		return nil, err
	}
	rows, err := sheetRows(raw, sheetContext{shared: shared, styleKinds: styleKinds, mac: mac})
	if err != nil {
		return nil, err
	}
	return build(rows, textual)
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

// columnIndex is the column of an 'A1' coordinate.
func columnIndex(reference string) (int, bool) {
	column, index := 0, 0
	for index < len(reference) && reference[index] >= 'A' && reference[index] <= 'Z' {
		column = column*26 + int(reference[index]-'A') + 1
		index++
	}
	if index == 0 || index > 3 || index == len(reference) || reference[index] == '0' {
		return 0, false
	}
	for _, c := range reference[index:] {
		if c < '0' || c > '9' {
			return 0, false
		}
	}
	return column, column <= 18278
}

// sheetRows is ReadOnlyWorksheet.rows after reset_dimensions(): the rows from
// row 1 with the cells from column 1 to each row's last cell.
func sheetRows(data []byte, context sheetContext) ([][]cell, error) {
	decoder := xml.NewDecoder(bytes.NewReader(data))
	decoder.CharsetReader = func(string, io.Reader) (io.Reader, error) { return nil, ErrUnsupported }
	var rows [][]cell
	rowCounter, depth, inData, root := 0, 0, false, false
	for {
		token, err := decoder.Token()
		if errors.Is(err, io.EOF) {
			break
		}
		if err != nil {
			return nil, ErrUnsupported
		}
		switch element := token.(type) {
		case xml.StartElement:
			depth++
			switch {
			case depth == 1:
				if element.Name.Space != mainNS || element.Name.Local != "worksheet" {
					return nil, ErrUnsupported
				}
				root = true
			case element.Name.Space == mainNS && element.Name.Local == "sheetData":
				if depth != 2 || inData {
					return nil, ErrUnsupported
				}
				inData = true
			case element.Name.Space == mainNS && element.Name.Local == "row":
				if !inData || depth != 3 {
					return nil, ErrUnsupported
				}
				var row sheetRow
				if err = decoder.DecodeElement(&row, &element); err != nil {
					return nil, ErrUnsupported
				}
				depth--
				if row.Number != nil {
					number, err := strconv.Atoi(*row.Number)
					if err != nil || number < 1 || number > 1048576 {
						return nil, ErrUnsupported
					}
					rowCounter = number
				} else {
					rowCounter++
				}
				cells, err := rowCells(row, context)
				if err != nil {
					return nil, err
				}
				if rowCounter <= len(rows) {
					continue // a row at or before the last one read is skipped
				}
				for len(rows) < rowCounter-1 {
					rows = append(rows, nil)
				}
				rows = append(rows, cells)
			}
		case xml.EndElement:
			if depth == 2 && element.Name.Local == "sheetData" {
				inData = false
			}
			depth--
		}
	}
	if !root {
		return nil, ErrUnsupported
	}
	return rows, nil
}
