package xlsxexport

import (
	_ "embed"
	"math"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

//go:embed parts/openpyxl-theme1.xml
var openpyxlTheme []byte

// opCell is an openpyxl cell after Cell._bind_value.
type opCell struct {
	col    int  // 1-based
	kind   byte // 's' string, 'f' formula, 'e' error code, 'n' number, 'b' bool
	text   string
	styled bool
	// style is a cell style index past the header's (the attendance
	// workbooks); 0 leaves styled to decide.
	style int
}

type opRow struct {
	row   int // 1-based
	cells []opCell
}

type opSheet struct {
	title  string
	rows   []opRow
	widths []string // pandas path: <col> width per column (nil: no <cols>)
}

var openpyxlIllegal = regexp.MustCompile(`[\x00-\x08\x0b\x0c\x0e-\x1f]`)

var openpyxlErrorCodes = map[string]bool{"#NULL!": true, "#DIV/0!": true, "#VALUE!": true, "#REF!": true, "#NAME?": true,
	"#NUM!": true, "#N/A": true}

// openpyxlString is Cell._bind_value of a str: truncated to 32767 characters,
// IllegalCharacterError for control characters, '=...' a formula, an error
// code an error cell.
func openpyxlString(col int, text string, styled bool) (opCell, error) {
	if pyLen(text) > 32767 {
		text = string([]rune(text)[:32767])
	}
	if openpyxlIllegal.MatchString(text) {
		return opCell{}, ErrDjango
	}
	cell := opCell{col: col, kind: 's', text: text, styled: styled}
	if pyLen(text) > 1 && strings.HasPrefix(text, "=") {
		cell.kind = 'f'
	} else if openpyxlErrorCodes[text] {
		cell.kind = 'e'
	}
	return cell, nil
}

// etreeText is ElementTree's character data escaping.
func etreeText(text string) string {
	if !strings.ContainsAny(text, "&<>") {
		return text
	}
	text = strings.ReplaceAll(text, "&", "&amp;")
	text = strings.ReplaceAll(text, "<", "&lt;")
	return strings.ReplaceAll(text, ">", "&gt;")
}

// pythonStripDiffers is openpyxl's whitespace(): the stripped text is not
// empty and differs from the text.
func pythonStripDiffers(text string) bool {
	stripped := strings.TrimFunc(text, dto.PythonIsSpace)
	return stripped != "" && stripped != text
}

// columnLetter is openpyxl's get_column_letter.
func columnLetter(col int) string {
	letters := ""
	for col > 0 {
		col--
		letters = string(rune('A'+col%26)) + letters
		col /= 26
	}
	return letters
}

func (cell opCell) write(out *strings.Builder, row int) {
	out.WriteString(`<c r="`)
	out.WriteString(columnLetter(cell.col))
	out.WriteString(strconv.Itoa(row))
	out.WriteByte('"')
	if cell.style > 0 {
		out.WriteString(` s="` + strconv.Itoa(cell.style) + `"`)
	} else if cell.styled {
		out.WriteString(` s="1"`)
	}
	switch cell.kind {
	case 's':
		out.WriteString(` t="inlineStr"`)
	case 'f':
	default:
		out.WriteString(` t="`)
		out.WriteByte(cell.kind)
		out.WriteByte('"')
	}
	if cell.text == "" {
		out.WriteString(" />")
		return
	}
	out.WriteByte('>')
	switch cell.kind {
	case 'f':
		out.WriteString("<f>")
		out.WriteString(etreeText(cell.text[1:]))
		out.WriteString("</f><v />")
	case 's':
		if pythonStripDiffers(cell.text) {
			out.WriteString(`<is><t xml:space="preserve">`)
		} else {
			out.WriteString("<is><t>")
		}
		out.WriteString(etreeText(cell.text))
		out.WriteString("</t></is>")
	default:
		out.WriteString("<v>")
		out.WriteString(etreeText(cell.text))
		out.WriteString("</v>")
	}
	out.WriteString("</c>")
}

func (sheet opSheet) xml() []byte {
	var out strings.Builder
	out.WriteString(`<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetPr><outlinePr summaryBelow="1" summaryRight="1" /><pageSetUpPr /></sheetPr>`)
	minRow, minCol, maxRow, maxCol := 0, 0, 0, 0
	for _, row := range sheet.rows {
		for _, cell := range row.cells {
			if minRow == 0 || row.row < minRow {
				minRow = row.row
			}
			if minCol == 0 || cell.col < minCol {
				minCol = cell.col
			}
			maxRow, maxCol = max(maxRow, row.row), max(maxCol, cell.col)
		}
	}
	dimension := "A1:A1"
	if minRow > 0 {
		dimension = columnLetter(minCol) + strconv.Itoa(minRow) + ":" + columnLetter(maxCol) + strconv.Itoa(maxRow)
	}
	out.WriteString(`<dimension ref="` + dimension + `" />`)
	out.WriteString(`<sheetViews><sheetView workbookViewId="0"><selection activeCell="A1" sqref="A1" /></sheetView></sheetViews><sheetFormatPr baseColWidth="8" defaultRowHeight="15" />`)
	if len(sheet.widths) > 0 {
		out.WriteString("<cols>")
		for i, width := range sheet.widths {
			index := strconv.Itoa(i + 1)
			out.WriteString(`<col width="` + width + `" customWidth="1" min="` + index + `" max="` + index + `" />`)
		}
		out.WriteString("</cols>")
	}
	written := false
	for _, row := range sheet.rows {
		if len(row.cells) == 0 {
			continue
		}
		if !written {
			out.WriteString("<sheetData>")
			written = true
		}
		out.WriteString(`<row r="` + strconv.Itoa(row.row) + `">`)
		for _, cell := range row.cells {
			cell.write(&out, row.row)
		}
		out.WriteString("</row>")
	}
	if written {
		out.WriteString("</sheetData>")
	} else {
		out.WriteString("<sheetData></sheetData>")
	}
	out.WriteString(`<pageMargins left="0.75" right="0.75" top="1" bottom="1" header="0.5" footer="0.5" /></worksheet>`)
	return []byte(out.String())
}

const openpyxlStylesHeader = `<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="0" /><fonts count="2"><font><name val="Calibri" /><family val="2" /><color theme="1" /><sz val="11" /><scheme val="minor" /></font><font><b val="1" /></font></fonts><fills count="2"><fill><patternFill /></fill><fill><patternFill patternType="gray125" /></fill></fills><borders count="2"><border><left /><right /><top /><bottom /><diagonal /></border><border><left style="thin" /><right style="thin" /><top style="thin" /><bottom style="thin" /></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" /></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" pivotButton="0" quotePrefix="0" xfId="0" /><xf numFmtId="0" fontId="1" fillId="0" borderId="1" applyAlignment="1" pivotButton="0" quotePrefix="0" xfId="0"><alignment horizontal="center" vertical="top" /></xf></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0" hidden="0" /></cellStyles><tableStyles count="0" defaultTableStyle="TableStyleMedium9" defaultPivotStyle="PivotStyleLight16" />`

const openpyxlStylesPlain = `<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="0" /><fonts count="1"><font><name val="Calibri" /><family val="2" /><color theme="1" /><sz val="11" /><scheme val="minor" /></font></fonts><fills count="2"><fill><patternFill /></fill><fill><patternFill patternType="gray125" /></fill></fills><borders count="1"><border><left /><right /><top /><bottom /><diagonal /></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" /></cellStyleXfs><cellXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" pivotButton="0" quotePrefix="0" xfId="0" /></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0" hidden="0" /></cellStyles><tableStyles count="0" defaultTableStyle="TableStyleMedium9" defaultPivotStyle="PivotStyleLight16" />`

const openpyxlIndexedColors = `<colors><indexedColors><rgbColor rgb="00000000" /><rgbColor rgb="00FFFFFF" /><rgbColor rgb="00FF0000" /><rgbColor rgb="0000FF00" /><rgbColor rgb="000000FF" /><rgbColor rgb="00FFFF00" /><rgbColor rgb="00FF00FF" /><rgbColor rgb="0000FFFF" /><rgbColor rgb="00000000" /><rgbColor rgb="00FFFFFF" /><rgbColor rgb="00FF0000" /><rgbColor rgb="0000FF00" /><rgbColor rgb="000000FF" /><rgbColor rgb="00FFFF00" /><rgbColor rgb="00FF00FF" /><rgbColor rgb="0000FFFF" /><rgbColor rgb="00800000" /><rgbColor rgb="00008000" /><rgbColor rgb="00000080" /><rgbColor rgb="00808000" /><rgbColor rgb="00800080" /><rgbColor rgb="00008080" /><rgbColor rgb="00C0C0C0" /><rgbColor rgb="00808080" /><rgbColor rgb="009999FF" /><rgbColor rgb="00993366" /><rgbColor rgb="00FFFFCC" /><rgbColor rgb="00CCFFFF" /><rgbColor rgb="00660066" /><rgbColor rgb="00FF8080" /><rgbColor rgb="000066CC" /><rgbColor rgb="00CCCCFF" /><rgbColor rgb="00000080" /><rgbColor rgb="00FF00FF" /><rgbColor rgb="00FFFF00" /><rgbColor rgb="0000FFFF" /><rgbColor rgb="00800080" /><rgbColor rgb="00800000" /><rgbColor rgb="00008080" /><rgbColor rgb="000000FF" /><rgbColor rgb="0000CCFF" /><rgbColor rgb="00CCFFFF" /><rgbColor rgb="00CCFFCC" /><rgbColor rgb="00FFFF99" /><rgbColor rgb="0099CCFF" /><rgbColor rgb="00FF99CC" /><rgbColor rgb="00CC99FF" /><rgbColor rgb="00FFCC99" /><rgbColor rgb="003366FF" /><rgbColor rgb="0033CCCC" /><rgbColor rgb="0099CC00" /><rgbColor rgb="00FFCC00" /><rgbColor rgb="00FF9900" /><rgbColor rgb="00FF6600" /><rgbColor rgb="00666699" /><rgbColor rgb="00969696" /><rgbColor rgb="00003366" /><rgbColor rgb="00339966" /><rgbColor rgb="00003300" /><rgbColor rgb="00333300" /><rgbColor rgb="00993300" /><rgbColor rgb="00993366" /><rgbColor rgb="00333399" /><rgbColor rgb="00333333" /></indexedColors></colors></styleSheet>`

// openpyxlPackage is Workbook.save: the parts in openpyxl's ExcelWriter order.
func openpyxlPackage(sheets []opSheet, styled bool, now time.Time) ([]byte, error) {
	return openpyxlPackageFlags(sheets, styled, now, 0)
}

func openpyxlPackageFlags(sheets []opSheet, styled bool, now time.Time, flags uint16) ([]byte, error) {
	stamp := now.UTC().Format("2006-01-02T15:04:05Z")
	core := `<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:creator>openpyxl</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">` +
		stamp + `</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">` + stamp + `</dcterms:modified></cp:coreProperties>`
	entries := []zipEntry{
		{name: "docProps/app.xml", external: externalFile, data: []byte(`<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>Microsoft Excel Compatible / Openpyxl 3.1.5</Application><AppVersion>3.1</AppVersion></Properties>`)},
		{name: "docProps/core.xml", external: externalFile, data: []byte(core)},
		{name: "xl/theme/theme1.xml", external: externalFile, data: openpyxlTheme},
	}
	for i, sheet := range sheets {
		entries = append(entries, zipEntry{name: "xl/worksheets/sheet" + strconv.Itoa(i+1) + ".xml", external: externalRegular, data: sheet.xml()})
	}
	// The header style exists once a header cell was written: a workbook
	// whose frames have no column keeps the plain styles.
	if styled {
		styled = false
		for _, sheet := range sheets {
			for _, row := range sheet.rows {
				for _, cell := range row.cells {
					styled = styled || cell.styled || cell.style != 0
				}
			}
		}
	}
	styles := openpyxlStylesPlain
	if styled {
		styles = openpyxlStylesHeader
	}
	extra := 0
	for _, sheet := range sheets {
		for _, row := range sheet.rows {
			for _, cell := range row.cells {
				extra = max(extra, cell.style-1)
			}
		}
	}
	if extra > 0 {
		// Alignment(textRotation=90, horizontal='center', vertical='center') set
		// on a header cell, then on a cell without a style.
		xfs := `<xf numFmtId="0" fontId="1" fillId="0" borderId="1" applyAlignment="1" pivotButton="0" quotePrefix="0" xfId="0"><alignment horizontal="center" vertical="center" textRotation="90" /></xf>`
		if extra > 1 {
			xfs += `<xf numFmtId="0" fontId="0" fillId="0" borderId="0" applyAlignment="1" pivotButton="0" quotePrefix="0" xfId="0"><alignment horizontal="center" vertical="center" textRotation="90" /></xf>`
		}
		styles = strings.Replace(styles, `<cellXfs count="2">`, `<cellXfs count="`+strconv.Itoa(2+extra)+`">`, 1)
		styles = strings.Replace(styles, `</cellXfs>`, xfs+`</cellXfs>`, 1)
	}
	var book, rels, types strings.Builder
	book.WriteString(`<workbook xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><workbookPr /><workbookProtection /><bookViews><workbookView visibility="visible" minimized="0" showHorizontalScroll="1" showVerticalScroll="1" showSheetTabs="1" tabRatio="600" firstSheet="0" activeTab="0" autoFilterDateGrouping="1" /></bookViews><sheets>`)
	rels.WriteString(`<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">`)
	types.WriteString(`<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml" /><Default Extension="xml" ContentType="application/xml" /><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml" /><Override PartName="/xl/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml" /><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml" /><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml" />`)
	for i, sheet := range sheets {
		n := strconv.Itoa(i + 1)
		book.WriteString(`<sheet name="` + etreeAttr(sheet.title) + `" sheetId="` + n + `" state="visible" r:id="rId` + n + `" />`)
		rels.WriteString(`<Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="/xl/worksheets/sheet` + n + `.xml" Id="rId` + n + `" />`)
		types.WriteString(`<Override PartName="/xl/worksheets/sheet` + n + `.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml" />`)
	}
	book.WriteString(`</sheets><definedNames /><calcPr calcId="124519" fullCalcOnLoad="1" /></workbook>`)
	next := len(sheets) + 1
	rels.WriteString(`<Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml" Id="rId` + strconv.Itoa(next) + `" />`)
	rels.WriteString(`<Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme" Target="theme/theme1.xml" Id="rId` + strconv.Itoa(next+1) + `" /></Relationships>`)
	types.WriteString(`<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml" /></Types>`)
	entries = append(entries,
		zipEntry{name: "xl/styles.xml", external: externalFile, data: []byte(styles + openpyxlIndexedColors)},
		zipEntry{name: "_rels/.rels", external: externalFile, data: []byte(`<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml" Id="rId1" /><Relationship Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml" Id="rId2" /><Relationship Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml" Id="rId3" /></Relationships>`)},
		zipEntry{name: "xl/workbook.xml", external: externalFile, data: []byte(book.String())},
		zipEntry{name: "xl/_rels/workbook.xml.rels", external: externalFile, data: []byte(rels.String())},
		zipEntry{name: "[Content_Types].xml", external: externalFile, data: []byte(types.String())},
	)
	return writeZipFlags(entries, now, flags)
}

// etreeAttr is ElementTree's attribute escaping.
func etreeAttr(text string) string {
	replacer := strings.NewReplacer("&", "&amp;", "<", "&lt;", ">", "&gt;", `"`, "&quot;", "\r", "&#13;", "\n", "&#10;", "\t", "&#09;")
	return replacer.Replace(text)
}

// PandasOpenpyxl is application.utils.excel_utils.get_excel_base64(*data,
// columns=columns) before the base64: DataFrame(list(zip(*data)), columns),
// the numeric coercion of every column whose non-null values all convert,
// Int64 for all-integer columns, widths min(max(len(str(value)),
// len(column)) + 2, 50) for columns A..Z, and df.to_excel(index=False)
// through openpyxl. ErrDjango is a workbook the function fails on (it
// returns None and the views raise). A name used by several columns makes
// df[col] a DataFrame: pd.to_numeric raises TypeError (the loop continues, the
// columns keep their inferred dtype) and DataFrame.astype(str).apply(len) is
// the column length, so their width is min(max(rows, len(column)) + 2, 50).
func PandasOpenpyxl(data [][]Value, columns []string, now time.Time) ([]byte, error) {
	rows := -1
	for _, column := range data {
		if rows < 0 || len(column) < rows {
			rows = len(column)
		}
	}
	if rows < 0 {
		rows = 0
	}
	if len(data) != len(columns) {
		return nil, ErrDjango // "N columns passed, passed data had M columns"
	}
	uses := map[string]int{}
	for _, name := range columns {
		uses[name]++
	}
	if len(columns) > 26 {
		return nil, ErrDjango // chr(65 + idx) is not a column name
	}
	frame := make([]frameColumn, len(columns))
	widths := make([]string, len(columns))
	for i := range columns {
		column, err := inferColumn(data[i][:rows])
		if err != nil {
			return nil, err
		}
		frame[i] = column
		if uses[columns[i]] > 1 {
			widths[i] = strconv.Itoa(min(max(rows, pyLen(columns[i]))+2, 50))
			continue
		}
		if column, err = excelUtilsCoerce(column); err != nil {
			return nil, err
		}
		frame[i] = column
		longest := -1
		for _, value := range column.values {
			longest = max(longest, pyLen(frameStr(column.dtype, value)))
		}
		if longest < 0 {
			widths[i] = "" // max(nan, ...) is nan: openpyxl writes ""
		} else {
			widths[i] = strconv.Itoa(min(max(longest, pyLen(columns[i]))+2, 50))
		}
	}
	sheet := opSheet{title: "Sheet1", widths: widths}
	header := opRow{row: 1}
	for i, name := range columns {
		cell, err := openpyxlString(i+1, name, true)
		if err != nil {
			return nil, err
		}
		header.cells = append(header.cells, cell)
	}
	sheet.rows = append(sheet.rows, header)
	for r := 0; r < rows; r++ {
		row := opRow{row: r + 2}
		for c, column := range frame {
			cell, err := pandasOpenpyxlCell(c+1, column.dtype, column.values[r])
			if err != nil {
				return nil, err
			}
			row.cells = append(row.cells, cell)
		}
		sheet.rows = append(sheet.rows, row)
	}
	return openpyxlPackage([]opSheet{sheet}, true, now)
}

// excelUtilsCoerce is get_excel_base64's per-column numeric conversion.
func excelUtilsCoerce(column frameColumn) (frameColumn, error) {
	present := frameColumn{dtype: column.dtype}
	for _, value := range column.values {
		if !isNA(value) {
			present.values = append(present.values, value)
		}
	}
	mask, err := toNumeric(present)
	if err != nil {
		return frameColumn{}, err
	}
	for _, value := range mask.values {
		if isNA(value) {
			return column, nil
		}
	}
	converted, err := toNumeric(column)
	if err != nil {
		return frameColumn{}, err
	}
	integral := true
	for _, value := range converted.values {
		switch {
		case value.Kind == Float && math.IsNaN(value.Float):
		case value.Kind == Float && (math.IsInf(value.Float, 0) || value.Float != math.Trunc(value.Float)):
			integral = false
		}
	}
	if converted.dtype == dtBool {
		return frameColumn{}, ErrNotPorted
	}
	if !integral {
		return frameColumn{dtype: dtFloat64, values: converted.values}, nil
	}
	out := frameColumn{dtype: dtNullable, values: make([]Value, len(converted.values))}
	for i, value := range converted.values {
		switch {
		case value.Kind == Int:
			out.values[i] = value
		case math.IsNaN(value.Float):
			out.values[i] = NoneValue()
		case math.Abs(value.Float) >= 1<<63:
			// astype('Int64') raises (caught): the column stays float64.
			return frameColumn{dtype: dtFloat64, values: converted.values}, nil
		default:
			out.values[i] = IntValue(int64(value.Float))
		}
	}
	return out, nil
}

// frameStr is astype(str) of one value of a column.
func frameStr(kind dtype, value Value) string {
	switch {
	case kind == dtNullable && value.Kind == None:
		return "<NA>"
	case kind == dtFloat64 && value.Kind == Float:
		return pythonFloatRepr(value.Float)
	}
	return pythonStrOf(value)
}

// pandasOpenpyxlCell is ExcelFormatter._format_value, _value_with_fmt and
// the openpyxl cell of one body value.
func pandasOpenpyxlCell(col int, kind dtype, value Value) (opCell, error) {
	switch value.Kind {
	case None:
		return opCell{col: col, kind: 's'}, nil
	case Float:
		switch {
		case math.IsNaN(value.Float):
			return opCell{col: col, kind: 's'}, nil
		case math.IsInf(value.Float, 1):
			return openpyxlString(col, "inf", false)
		case math.IsInf(value.Float, -1):
			return openpyxlString(col, "-inf", false)
		}
		return opCell{col: col, kind: 'n', text: percentG(value.Float, false)}, nil
	case Int:
		return opCell{col: col, kind: 'n', text: percentG(float64(value.Int), false)}, nil
	case Bool:
		return opCell{}, ErrNotPorted
	}
	return openpyxlString(col, value.Text, false)
}

// OpenpyxlRows is a plain openpyxl Workbook whose default sheet is removed,
// with one sheet per title and the rows appended as str values.
func OpenpyxlRows(titles []string, sheets [][][]string, now time.Time) ([]byte, error) {
	return openpyxlRows(titles, sheets, now, 0)
}

// OpenpyxlRowsResponse is OpenpyxlRows saved to a stream zipfile cannot seek
// (export_all_data's wb.save(HttpResponse)): every entry has general purpose
// flag 0x08 and a data descriptor.
func OpenpyxlRowsResponse(titles []string, sheets [][][]string, now time.Time) ([]byte, error) {
	return openpyxlRows(titles, sheets, now, zipDataDescriptor)
}

func openpyxlRows(titles []string, sheets [][][]string, now time.Time, flags uint16) ([]byte, error) {
	out := make([]opSheet, len(titles))
	for i, title := range titles {
		out[i].title = title
		for r, values := range sheets[i] {
			row := opRow{row: r + 1}
			for c, text := range values {
				cell, err := openpyxlString(c+1, text, false)
				if err != nil {
					return nil, err
				}
				row.cells = append(row.cells, cell)
			}
			out[i].rows = append(out[i].rows, row)
		}
	}
	return openpyxlPackageFlags(out, false, now, flags)
}
