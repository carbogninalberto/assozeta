package pandasxlsx

import (
	"archive/zip"
	"bytes"
	"encoding/xml"
	"errors"
	"io"
	"math"
	"path"
	"strconv"
	"strings"
	"time"

	"github.com/dlclark/regexp2"
)

const (
	mainNS         = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
	relationNS     = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
	packageRelNS   = "http://schemas.openxmlformats.org/package/2006/relationships"
	contentTypesNS = "http://schemas.openxmlformats.org/package/2006/content-types"
	worksheetType  = relationNS + "/worksheet"
	stringsType    = relationNS + "/sharedStrings"
	stylesType     = relationNS + "/styles"
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
	escapedText     = regexp2.MustCompile(`_x[0-9A-Fa-f]{4}_|x005F_`, regexp2.None)
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

// Read is pandas.read_excel(io.BytesIO(data), engine="openpyxl").
func Read(data []byte) (*Frame, error) {
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
	workbookPart := ""
	for _, override := range types.Overrides {
		if workbookTypes[override.Type] {
			workbookPart = strings.TrimPrefix(override.Part, "/")
			break
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
	if workbook.Properties.Date1904 != "" && workbook.Properties.Date1904 != "0" && workbook.Properties.Date1904 != "false" {
		return nil, ErrUnsupported
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
	stringsPart, stylesPart := "", ""
	for _, item := range rels.Items {
		if item.Mode != "" || item.ID == "" {
			return nil, ErrUnsupported
		}
		if _, duplicate := targets[item.ID]; duplicate {
			return nil, ErrUnsupported
		}
		targets[item.ID], kinds[item.ID] = partName(workbookPart, item.Target), item.Type
		switch item.Type {
		case stringsType:
			stringsPart = targets[item.ID]
		case stylesType:
			stylesPart = targets[item.ID]
		}
	}
	if len(workbook.Sheets) == 0 || stylesPart == "" {
		return nil, ErrUnsupported
	}
	// read_excel's sheet 0: the first worksheet (chartsheets are not worksheets).
	first := workbook.Sheets[0].ID
	if kinds[first] != worksheetType {
		return nil, ErrUnsupported
	}
	var shared []string
	if stringsPart != "" {
		if _, ok := files[stringsPart]; ok {
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
				shared = append(shared, item.content())
			}
		}
	}
	if raw, err = files.read(stylesPart); err != nil {
		return nil, err
	}
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
	if err = decode(raw, &styles); err != nil {
		return nil, err
	}
	custom := map[int]string{}
	for _, format := range styles.Formats {
		id, err := strconv.Atoi(format.ID)
		if err != nil {
			return nil, ErrUnsupported
		}
		custom[id] = format.Code
	}
	// 0 number, 1 date, 2 timedelta (refused when a cell uses it).
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
		}
		if known && isTimedeltaFormat(format) {
			styleKinds[index] = 2
		}
	}
	if raw, err = files.read(targets[first]); err != nil {
		return nil, err
	}
	rows, err := sheetRows(raw, shared, styleKinds)
	if err != nil {
		return nil, err
	}
	return build(rows)
}

type sheetCell struct {
	XMLName   xml.Name
	Reference string   `xml:"r,attr"`
	Type      *string  `xml:"t,attr"`
	Style     string   `xml:"s,attr"`
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
func sheetRows(data []byte, shared []string, styleKinds []int) ([][]cell, error) {
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
				cells, err := rowCells(row, shared, styleKinds)
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

func rowCells(row sheetRow, shared []string, styleKinds []int) ([]cell, error) {
	type placed struct {
		column int
		value  cell
	}
	var cells []placed
	counter := 0
	for _, item := range row.Cells {
		if item.XMLName.Space != mainNS || item.XMLName.Local != "c" || len(item.Values) > 1 || len(item.Inline) > 1 {
			return nil, ErrUnsupported
		}
		if item.Reference != "" {
			column, ok := columnIndex(item.Reference)
			if !ok {
				return nil, ErrUnsupported
			}
			counter = column
		} else {
			counter++
		}
		kind := "n"
		if item.Type != nil {
			kind = *item.Type
		}
		style := 0
		if item.Style != "" {
			var err error
			if style, err = strconv.Atoi(item.Style); err != nil || style < 0 {
				return nil, ErrUnsupported
			}
		}
		value := cell{}
		raw := ""
		if len(item.Values) == 1 && kind != "inlineStr" {
			raw = item.Values[0]
		}
		switch {
		case kind == "inlineStr":
			if len(item.Inline) == 1 {
				value = cell{kind: cellString, s: item.Inline[0].content()}
			}
		case raw == "":
			if kind != "n" && kind != "s" && kind != "str" {
				return nil, ErrUnsupported
			}
		case kind == "n":
			number, err := castNumber(raw)
			if err != nil {
				return nil, err
			}
			value = number
			if style >= len(styleKinds) {
				return nil, ErrUnsupported
			}
			switch styleKinds[style] {
			case 1:
				if value, err = fromExcel(number); err != nil {
					return nil, err
				}
			case 2:
				return nil, ErrUnsupported
			}
		case kind == "s":
			index, err := strconv.Atoi(raw)
			if err != nil || index < 0 || index >= len(shared) {
				return nil, ErrUnsupported
			}
			value = cell{kind: cellString, s: shared[index]}
		case kind == "str":
			value = cell{kind: cellString, s: raw}
		default:
			return nil, ErrUnsupported // booleans, errors, ISO dates
		}
		if value.kind == cellString && matches(escapedText, value.s) {
			return nil, ErrUnsupported
		}
		cells = append(cells, placed{column: counter, value: value})
	}
	if len(cells) == 0 {
		return nil, nil
	}
	// The row is as wide as its last cell's column.
	width := cells[len(cells)-1].column
	if width < 1 {
		return nil, ErrUnsupported
	}
	out := make([]cell, width)
	for _, item := range cells {
		if item.column >= 1 && item.column <= width {
			out[item.column-1] = item.value
		}
	}
	return out, nil
}

// castNumber is openpyxl's _cast_number followed by pandas' _convert_cell:
// an integral float is an int.
func castNumber(raw string) (cell, error) {
	if raw == "" || strings.Trim(raw, "0123456789+-.eE") != "" {
		return cell{}, ErrUnsupported
	}
	if !strings.ContainsAny(raw, ".eE") {
		value, err := strconv.ParseInt(raw, 10, 64)
		if err != nil {
			return cell{}, ErrUnsupported
		}
		return cell{kind: cellInt, i: value}, nil
	}
	value, err := strconv.ParseFloat(raw, 64)
	if err != nil || math.IsInf(value, 0) || math.IsNaN(value) {
		return cell{}, ErrUnsupported
	}
	if value == math.Trunc(value) {
		if math.Abs(value) >= 1<<63 {
			return cell{}, ErrUnsupported
		}
		return cell{kind: cellInt, i: int64(value)}, nil
	}
	return cell{kind: cellFloat, f: value}, nil
}

var windowsEpoch = time.Date(1899, 12, 30, 0, 0, 0, 0, time.UTC)

// fromExcel is openpyxl's from_excel for the 1900 date system, for serials
// that give a datetime (from 1 to the end of year 9999).
func fromExcel(number cell) (cell, error) {
	value := number.f
	if number.kind == cellInt {
		value = float64(number.i)
	}
	if value < 1 || value >= 2958466 {
		return cell{}, ErrUnsupported // a time of day, or outside datetime
	}
	day, fraction := math.Modf(value)
	milliseconds := math.RoundToEven(fraction * 86400 * 1000)
	if value < 60 {
		day++
	}
	moment := windowsEpoch.AddDate(0, 0, int(day)).Add(time.Duration(milliseconds) * time.Millisecond)
	if moment.Year() > 9999 {
		return cell{}, ErrUnsupported
	}
	return cell{kind: cellTime, t: moment}, nil
}
