package pandasxlsx

import (
	"archive/zip"
	"bytes"
	"errors"
	"strings"
	"testing"
)

// The parts of a one-sheet workbook with no styles: what the fixtures of
// TESTS/adapters/go/pandas_xlsx_golden.py write, in Go.
func testWorkbook(t *testing.T, sheet, strings string) []byte {
	t.Helper()
	parts := [][2]string{
		{"[Content_Types].xml", `<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">` +
			`<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>` +
			`<Default Extension="xml" ContentType="application/xml"/>` +
			`<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>` +
			`<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>` +
			`<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>` +
			`</Types>`},
		{"_rels/.rels", `<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
			`<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>` +
			`</Relationships>`},
		{"xl/workbook.xml", `<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" ` +
			`xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">` +
			`<sheets><sheet name="Soci" sheetId="1" r:id="rId1"/></sheets></workbook>`},
		{"xl/_rels/workbook.xml.rels", `<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">` +
			`<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>` +
			`<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>` +
			`</Relationships>`},
		{"xl/worksheets/sheet1.xml", sheet},
		{"xl/sharedStrings.xml", strings},
	}
	var buffer bytes.Buffer
	bundle := zip.NewWriter(&buffer)
	for _, part := range parts {
		file, err := bundle.Create(part[0])
		if err != nil {
			t.Fatal(err)
		}
		if _, err = file.Write([]byte(part[1])); err != nil {
			t.Fatal(err)
		}
	}
	if err := bundle.Close(); err != nil {
		t.Fatal(err)
	}
	return buffer.Bytes()
}

func sheetOf(body string) string {
	return `<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">` +
		`<sheetData>` + body + `</sheetData></worksheet>`
}

func stringsOf(items ...string) string {
	return `<?xml version="1.0" encoding="UTF-8"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">` +
		strings.Join(items, "") + `</sst>`
}

const testRow = `<row r="1"><c r="A1" t="inlineStr"><is><t>Nome</t></is></c><c r="B1"><v>1</v></c></row>`

// TestPartBounds is the size bound: the parts openpyxl parses whole (the
// workbook, its relationships, the content types, the shared strings) are left
// to Django past maxPartBytes, because pandas needs that much memory for them
// on top of the rest of the request; the worksheet is read as it is parsed, as
// openpyxl's iterparse does, so its size alone is no refusal.
func TestPartBounds(t *testing.T) {
	saved := maxPartBytes
	maxPartBytes = 1024
	t.Cleanup(func() { maxPartBytes = saved })

	padding := "<!--" + strings.Repeat("x", 4096) + "-->"
	sheet := strings.Replace(sheetOf(testRow), "<sheetData>", padding+"<sheetData>", 1)
	data := testWorkbook(t, sheet, stringsOf("<si><t>Nome</t></si>"))
	frame, err := ReadAll(data, nil)
	if err != nil {
		t.Fatalf("a worksheet past the bound: %v", err)
	}
	if len(frame.Columns) != 2 || frame.Columns[0] != "Nome" {
		t.Fatalf("worksheet past the bound: %v", frame.Columns)
	}

	data = testWorkbook(t, sheetOf(testRow), stringsOf("<si><t>"+strings.Repeat("y", 4096)+"</t></si>"))
	if _, err = ReadAll(data, nil); !errors.Is(err, ErrWorkbookNotPorted) {
		t.Fatalf("a shared string table past the bound: %v", err)
	}
}
