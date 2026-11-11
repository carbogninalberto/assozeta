package xlsxexport

import (
	_ "embed"
	"math"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

//go:embed parts/xlsxwriter-theme1.xml
var xlsxwriterTheme []byte

// Frame is a DataFrame written by pandas' xlsxwriter engine:
// df.to_excel(writer, sheet_name=Sheet, index=Index), then
// worksheet.set_column(i, i, Widths[i]) and writer.close().
type Frame struct {
	Sheet   string
	Columns []string
	Rows    [][]Value
	Index   bool
	Widths  []float64
}

// xwCell is one stored xlsxwriter cell.
type xwCell struct {
	kind   byte // 's' string, 'n' number, 'b' bool, 'k' blank
	number string
	sst    int
	format int // 0 none, 1 header, 2 hyperlink
}

type xwLink struct {
	row, col int
	url      string
}

type xwSheet struct {
	cells   map[int]map[int]xwCell
	strings []string
	index   map[string]int
	count   int
	links   []xwLink
	rowMin  int
	rowMax  int
	colMin  int
	colMax  int
	used    bool
}

func (sheet *xwSheet) store(row, col int, cell xwCell) {
	if sheet.cells[row] == nil {
		sheet.cells[row] = map[int]xwCell{}
	}
	sheet.cells[row][col] = cell
	if !sheet.used {
		sheet.rowMin, sheet.rowMax, sheet.colMin, sheet.colMax, sheet.used = row, row, col, col, true
	}
	sheet.rowMin, sheet.rowMax = min(sheet.rowMin, row), max(sheet.rowMax, row)
	sheet.colMin, sheet.colMax = min(sheet.colMin, col), max(sheet.colMax, col)
}

func (sheet *xwSheet) writeString(row, col int, text string, format int) {
	if utf8.RuneCountInString(text) > 32767 {
		text = string([]rune(text)[:32767])
	}
	index, ok := sheet.index[text]
	if !ok {
		index = len(sheet.strings)
		sheet.index[text] = index
		sheet.strings = append(sheet.strings, text)
	}
	sheet.count++
	sheet.store(row, col, xwCell{kind: 's', sst: index, format: format})
}

func xwLooksRich(text string) bool {
	return strings.HasPrefix(text, "<r>") && strings.HasSuffix(text, "</r>")
}

// xwRichRun is application.utils.xlsx_text.xlsx_cell_text for text that looks
// like rich-string markup.
func xwRichRun(text string) string {
	return `<r><t xml:space="preserve">` + xwEscapeData(text) + `</t></r>`
}

var xwURLPrefix = regexp.MustCompile(`^((ftp|http)s?://|mailto:|(in|ex)ternal:)`)
var xwEscaped = regexp.MustCompile(`%[0-9a-fA-F]{2}`)

// writeToken is Worksheet.write of a str (_write_token_as_string with the
// defaults strings_to_formulas and strings_to_urls).
func (sheet *xwSheet) writeToken(row, col int, text string, format int) error {
	switch {
	case text == "":
		if format != 0 {
			sheet.store(row, col, xwCell{kind: 'k', format: format})
		}
		return nil
	case strings.HasPrefix(text, "="), strings.HasPrefix(text, "{=") && strings.HasSuffix(text, "}"):
		return ErrNotPorted // formulas (_prepare_formula) are not reproduced
	case xwLooksRich(text):
		// The Django export hands such text over as one rich-string run with
		// the characters escaped (xlsx_text.py), so the cell shows the text.
		sheet.writeString(row, col, xwRichRun(text), format)
		return nil
	case strings.Contains(text, ":") && xwURLPrefix.MatchString(text):
		return sheet.writeURL(row, col, text, format)
	}
	sheet.writeString(row, col, text, format)
	return nil
}

// writeURL is _write_url for external http(s)/ftp(s)/mailto links without
// an anchor.
func (sheet *xwSheet) writeURL(row, col int, url string, format int) error {
	if strings.HasPrefix(url, "internal:") || strings.HasPrefix(url, "external:") || strings.Contains(url, "#") || format != 0 {
		return ErrNotPorted
	}
	display := strings.ReplaceAll(url, "mailto:", "")
	if utf8.RuneCountInString(display) > 32767 {
		return nil // ignored: nothing is written
	}
	if !xwEscaped.MatchString(url) {
		url = strings.NewReplacer("%", "%25", `"`, "%22", " ", "%20", "<", "%3c", ">", "%3e", "[", "%5b", "]", "%5d",
			"^", "%5e", "`", "%60", "{", "%7b", "}", "%7d").Replace(url)
	}
	if utf8.RuneCountInString(url) > 2079 {
		return nil // ignored: nothing is written
	}
	if len(sheet.links) >= 65530 {
		return nil
	}
	sheet.writeString(row, col, display, 2)
	sheet.links = append(sheet.links, xwLink{row: row, col: col, url: url})
	return nil
}

// writeValue is pandas' _format_value and _value_with_fmt, then Worksheet.write.
func (sheet *xwSheet) writeValue(row, col int, value Value, format int) error {
	switch value.Kind {
	case None:
		return sheet.writeToken(row, col, "", format)
	case Float:
		switch {
		case math.IsNaN(value.Float):
			return sheet.writeToken(row, col, "", format)
		case math.IsInf(value.Float, 1):
			return sheet.writeToken(row, col, "inf", format)
		case math.IsInf(value.Float, -1):
			return sheet.writeToken(row, col, "-inf", format)
		}
		sheet.store(row, col, xwCell{kind: 'n', number: percentG(value.Float, true), format: format})
	case Int:
		sheet.store(row, col, xwCell{kind: 'n', number: percentG(float64(value.Int), true), format: format})
	case Bool:
		text := "0"
		if value.Bool {
			text = "1"
		}
		sheet.store(row, col, xwCell{kind: 'b', number: text, format: format})
	default:
		return sheet.writeToken(row, col, value.Text, format)
	}
	return nil
}

// xwEscapeData is xlsxwriter's _escape_data.
func xwEscapeData(text string) string {
	if !strings.ContainsAny(text, "&<>") {
		return text
	}
	return strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;").Replace(text)
}

// xwEscapeAttr is xlsxwriter's _escape_attributes.
func xwEscapeAttr(text string) string {
	return strings.NewReplacer("&", "&amp;", `"`, "&quot;", "<", "&lt;", ">", "&gt;", "\n", "&#xA;").Replace(text)
}

var xwControlEscaped = regexp.MustCompile(`_x[0-9a-fA-F]{4}_`)

// xwEscapeControl is _escape_control_characters.
func xwEscapeControl(text string) string {
	text = xwControlEscaped.ReplaceAllStringFunc(text, func(match string) string { return "_x005F" + match })
	var out strings.Builder
	for _, r := range text {
		switch {
		case r <= 0x08 || (r >= 0x0b && r <= 0x1f):
			out.WriteString("_x")
			hex := strings.ToUpper(strconv.FormatInt(int64(r), 16))
			out.WriteString(strings.Repeat("0", 4-len(hex)) + hex)
			out.WriteByte('_')
		case r == 0xFFFE:
			out.WriteString("_xFFFE_")
		case r == 0xFFFF:
			out.WriteString("_xFFFF_")
		default:
			out.WriteRune(r)
		}
	}
	return out.String()
}

// xwCellRef is xl_rowcol_to_cell.
func xwCellRef(row, col int) string { return columnLetter(col+1) + strconv.Itoa(row+1) }

// xwWidth is _write_col_info's width conversion for Calibri 11.
func xwWidth(width float64) string {
	if width > 0 {
		if width < 1 {
			width = float64(int(float64(int(width*12+0.5))/7.0*256.0)) / 256.0
		} else {
			width = float64(int(float64(int(width*7+0.5)+5)/7.0*256.0)) / 256.0
		}
	}
	return strconv.FormatFloat(width, 'g', 16, 64)
}

func (sheet *xwSheet) xml(widths []float64) []byte {
	var out strings.Builder
	out.WriteString("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n")
	out.WriteString(`<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">`)
	ref := "A1"
	if sheet.used {
		ref = xwCellRef(sheet.rowMin, sheet.colMin)
		if sheet.rowMin != sheet.rowMax || sheet.colMin != sheet.colMax {
			ref += ":" + xwCellRef(sheet.rowMax, sheet.colMax)
		}
	}
	out.WriteString(`<dimension ref="` + ref + `"/><sheetViews><sheetView tabSelected="1" workbookViewId="0"/></sheetViews><sheetFormatPr defaultRowHeight="15"/>`)
	if len(widths) > 0 {
		out.WriteString("<cols>")
		first := 0
		for col := 1; col <= len(widths); col++ {
			if col < len(widths) && widths[col] == widths[first] {
				continue
			}
			attrs := `<col min="` + strconv.Itoa(first+1) + `" max="` + strconv.Itoa(col) + `" width="` + xwWidth(widths[first]) + `"`
			if widths[first] != 8.43 {
				attrs += ` customWidth="1"`
			}
			out.WriteString(attrs + "/>")
			first = col
		}
		out.WriteString("</cols>")
	}
	if !sheet.used {
		out.WriteString("<sheetData/>")
	} else {
		out.WriteString("<sheetData>")
		spans := map[int]string{}
		for block := sheet.rowMin / 16; block <= sheet.rowMax/16; block++ {
			low, high := -1, -1
			for row := max(block*16, sheet.rowMin); row <= min(block*16+15, sheet.rowMax); row++ {
				for col := range sheet.cells[row] {
					if low < 0 || col < low {
						low = col
					}
					high = max(high, col)
				}
			}
			if low >= 0 {
				spans[block] = strconv.Itoa(low+1) + ":" + strconv.Itoa(high+1)
			}
		}
		for row := sheet.rowMin; row <= sheet.rowMax; row++ {
			cells := sheet.cells[row]
			if len(cells) == 0 {
				continue
			}
			out.WriteString(`<row r="` + strconv.Itoa(row+1) + `"`)
			if span, ok := spans[row/16]; ok {
				out.WriteString(` spans="` + span + `"`)
			}
			out.WriteByte('>')
			cols := make([]int, 0, len(cells))
			for col := range cells {
				cols = append(cols, col)
			}
			sort.Ints(cols)
			for _, col := range cols {
				cell := cells[col]
				out.WriteString(`<c r="` + xwCellRef(row, col) + `"`)
				if cell.format != 0 {
					out.WriteString(` s="` + strconv.Itoa(cell.format) + `"`)
				}
				switch cell.kind {
				case 'k':
					out.WriteString("/>")
					continue
				case 's':
					out.WriteString(` t="s"><v>` + strconv.Itoa(cell.sst) + `</v></c>`)
				case 'b':
					out.WriteString(` t="b"><v>` + cell.number + `</v></c>`)
				default:
					out.WriteString(`><v>` + cell.number + `</v></c>`)
				}
			}
			out.WriteString("</row>")
		}
		out.WriteString("</sheetData>")
	}
	if len(sheet.links) > 0 {
		links := append([]xwLink{}, sheet.links...)
		sort.SliceStable(links, func(i, j int) bool {
			if links[i].row != links[j].row {
				return links[i].row < links[j].row
			}
			return links[i].col < links[j].col
		})
		sheet.links = links
		out.WriteString("<hyperlinks>")
		for i, link := range links {
			out.WriteString(`<hyperlink ref="` + xwCellRef(link.row, link.col) + `" r:id="rId` + strconv.Itoa(i+1) + `"/>`)
		}
		out.WriteString("</hyperlinks>")
	}
	out.WriteString(`<pageMargins left="0.7" right="0.7" top="0.75" bottom="0.75" header="0.3" footer="0.3"/></worksheet>`)
	return []byte(out.String())
}

func (sheet *xwSheet) usesFormat(format int) bool {
	for _, row := range sheet.cells {
		for _, cell := range row {
			if cell.format == format {
				return true
			}
		}
	}
	return false
}

const xwStylesHead = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n" + `<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">`
const xwFontNormal = `<font><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/><scheme val="minor"/></font>`
const xwFontBold = `<font><b/><sz val="11"/><color theme="1"/><name val="Calibri"/><family val="2"/><scheme val="minor"/></font>`
const xwFontLink = `<font><u/><sz val="11"/><color theme="10"/><name val="Calibri"/><family val="2"/></font>`
const xwFills = `<fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills>`
const xwBorderNone = `<border><left/><right/><top/><bottom/><diagonal/></border>`
const xwBorderThin = `<border><left style="thin"><color auto="1"/></left><right style="thin"><color auto="1"/></right><top style="thin"><color auto="1"/></top><bottom style="thin"><color auto="1"/></bottom><diagonal/></border>`
const xwXfNormal = `<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>`
const xwXfHeader = `<xf numFmtId="0" fontId="1" fillId="0" borderId="1" xfId="0" applyFont="1" applyBorder="1" applyAlignment="1"><alignment horizontal="center" vertical="top"/></xf>`
const xwStylesTail = `<dxfs count="0"/><tableStyles count="0" defaultTableStyle="TableStyleMedium9" defaultPivotStyle="PivotStyleLight16"/></styleSheet>`

// xwStyles is styles.xml for the formats in use: none, the pandas header,
// and the header plus the default hyperlink format.
func xwStyles(header, link bool) string {
	switch {
	case link:
		return xwStylesHead + `<fonts count="3">` + xwFontNormal + xwFontBold + xwFontLink + `</fonts>` + xwFills +
			`<borders count="2">` + xwBorderNone + xwBorderThin + `</borders>` +
			`<cellStyleXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/><xf numFmtId="0" fontId="2" fillId="0" borderId="0" applyNumberFormat="0" applyFill="0" applyBorder="0" applyAlignment="0" applyProtection="0"><alignment vertical="top"/><protection locked="0"/></xf></cellStyleXfs>` +
			`<cellXfs count="3">` + xwXfNormal + xwXfHeader + `<xf numFmtId="0" fontId="2" fillId="0" borderId="0" xfId="1" applyAlignment="1" applyProtection="1"/></cellXfs>` +
			`<cellStyles count="2"><cellStyle name="Hyperlink" xfId="1" builtinId="8"/><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>` + xwStylesTail
	case header:
		return xwStylesHead + `<fonts count="2">` + xwFontNormal + xwFontBold + `</fonts>` + xwFills +
			`<borders count="2">` + xwBorderNone + xwBorderThin + `</borders>` +
			`<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>` +
			`<cellXfs count="2">` + xwXfNormal + xwXfHeader + `</cellXfs>` +
			`<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>` + xwStylesTail
	}
	return xwStylesHead + `<fonts count="1">` + xwFontNormal + `</fonts>` + xwFills + `<borders count="1">` + xwBorderNone + `</borders>` +
		`<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>` +
		`<cellXfs count="1">` + xwXfNormal + `</cellXfs>` +
		`<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>` + xwStylesTail
}

// PandasXlsxwriter builds the workbook of a Frame. Cells are written as
// pandas yields them (header, index, then the body column by column), which
// fixes the shared string order.
func PandasXlsxwriter(frame Frame, now time.Time) ([]byte, error) {
	if frame.Sheet == "" || len(frame.Sheet) > 31 || strings.ContainsAny(frame.Sheet, `[]:*?/\'`) {
		return nil, ErrNotPorted
	}
	sheet := &xwSheet{cells: map[int]map[int]xwCell{}, index: map[string]int{}}
	offset := 0
	if frame.Index {
		offset = 1
	}
	for i, name := range frame.Columns {
		if strings.Contains(name, ":") && xwURLPrefix.MatchString(name) {
			return nil, ErrNotPorted
		}
		if err := sheet.writeToken(0, i+offset, name, 1); err != nil {
			return nil, err
		}
	}
	if frame.Index {
		for r := range frame.Rows {
			sheet.store(r+1, 0, xwCell{kind: 'n', number: strconv.Itoa(r), format: 1})
		}
	}
	for c := range frame.Columns {
		for r, row := range frame.Rows {
			if len(row) != len(frame.Columns) {
				return nil, ErrDjango
			}
			if err := sheet.writeValue(r+1, c+offset, row[c], 0); err != nil {
				return nil, err
			}
		}
	}
	data := sheet.xml(frame.Widths)
	stamp := now.UTC().Format("2006-01-02T15:04:05Z")
	header, link := sheet.usesFormat(1), len(sheet.links) > 0
	sharedStrings := len(sheet.strings) > 0
	types := "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n" + `<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`
	rels := "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n" + `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>`
	if sharedStrings {
		types += `<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>`
		rels += `<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>`
	}
	types += "</Types>"
	rels += "</Relationships>"
	entries := []zipEntry{
		{name: "[Content_Types].xml", data: []byte(types)},
		{name: "_rels/.rels", data: []byte("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n" + `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>`)},
		{name: "xl/_rels/workbook.xml.rels", data: []byte(rels)},
		{name: "xl/worksheets/sheet1.xml", data: data},
		{name: "xl/workbook.xml", data: []byte("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n" + `<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><fileVersion appName="xl" lastEdited="4" lowestEdited="4" rupBuild="4505"/><workbookPr defaultThemeVersion="124226"/><bookViews><workbookView xWindow="240" yWindow="15" windowWidth="16095" windowHeight="9660"/></bookViews><sheets><sheet name="` + xwEscapeAttr(frame.Sheet) + `" sheetId="1" r:id="rId1"/></sheets><calcPr calcId="124519" fullCalcOnLoad="1"/></workbook>`)},
	}
	if sharedStrings {
		var sst strings.Builder
		sst.WriteString("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n")
		sst.WriteString(`<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" count="` + strconv.Itoa(sheet.count) + `" uniqueCount="` + strconv.Itoa(len(sheet.strings)) + `">`)
		for _, text := range sheet.strings {
			text = xwEscapeControl(text)
			if xwLooksRich(text) {
				// xlsxwriter writes a string of this shape without further tags.
				sst.WriteString("<si>" + text + "</si>")
				continue
			}
			first, _ := utf8.DecodeRuneInString(text)
			last, _ := utf8.DecodeLastRuneInString(text)
			if dto.PythonIsSpace(first) || dto.PythonIsSpace(last) {
				sst.WriteString(`<si><t xml:space="preserve">`)
			} else {
				sst.WriteString("<si><t>")
			}
			sst.WriteString(xwEscapeData(text) + "</t></si>")
		}
		sst.WriteString("</sst>")
		entries = append(entries, zipEntry{name: "xl/sharedStrings.xml", data: []byte(sst.String())})
	}
	entries = append(entries,
		zipEntry{name: "xl/styles.xml", data: []byte(xwStyles(header, link))},
		zipEntry{name: "xl/theme/theme1.xml", data: xlsxwriterTheme})
	if link {
		var out strings.Builder
		out.WriteString("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n" + `<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">`)
		for i, item := range sheet.links {
			out.WriteString(`<Relationship Id="rId` + strconv.Itoa(i+1) + `" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink" Target="` + xwEscapeAttr(item.url) + `" TargetMode="External"/>`)
		}
		out.WriteString("</Relationships>")
		entries = append(entries, zipEntry{name: "xl/worksheets/_rels/sheet1.xml.rels", data: []byte(out.String())})
	}
	entries = append(entries,
		zipEntry{name: "docProps/core.xml", data: []byte("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n" + `<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:creator></dc:creator><cp:lastModifiedBy></cp:lastModifiedBy><dcterms:created xsi:type="dcterms:W3CDTF">` + stamp + `</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">` + stamp + `</dcterms:modified></cp:coreProperties>`)},
		zipEntry{name: "docProps/app.xml", data: []byte("<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n" + `<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties" xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"><Application>Microsoft Excel</Application><DocSecurity>0</DocSecurity><ScaleCrop>false</ScaleCrop><HeadingPairs><vt:vector size="2" baseType="variant"><vt:variant><vt:lpstr>Worksheets</vt:lpstr></vt:variant><vt:variant><vt:i4>1</vt:i4></vt:variant></vt:vector></HeadingPairs><TitlesOfParts><vt:vector size="1" baseType="lpstr"><vt:lpstr>` + xwEscapeData(frame.Sheet) + `</vt:lpstr></vt:vector></TitlesOfParts><Company></Company><LinksUpToDate>false</LinksUpToDate><SharedDoc>false</SharedDoc><HyperlinksChanged>false</HyperlinksChanged><AppVersion>12.0000</AppVersion></Properties>`)})
	for i := range entries {
		entries[i].external = externalRegular
	}
	return writeZip(entries, now)
}

// StrLength is max(len(str(value))) of DataFrame(...)[column].astype(str)
// for the column's values (after the DataFrame's dtype inference), -1 for no
// rows.
func StrLength(values []Value) (int, error) {
	column, err := inferColumn(values)
	if err != nil {
		return 0, err
	}
	longest := -1
	for _, value := range column.values {
		longest = max(longest, pyLen(frameStr(column.dtype, value)))
	}
	return longest, nil
}
