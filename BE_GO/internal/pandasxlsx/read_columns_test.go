package pandasxlsx

import (
	"errors"
	"testing"
)

// TestReadColumns is the upload's answer for a worksheet of any size: the
// columns do not depend on the rows, so ReadColumns streams them. ReadAll
// stops at a row numbered past maxRowNumber with ErrRowsPastBound (the caller
// answers as when the read runs out of memory); ReadColumns answers the
// columns, still converting every cell (a cell openpyxl raises on raises).
//
// Expected values: pandas 2.2.3 with openpyxl 3.1.5 (scratch/w3/probe_rows.py),
// on workbooks whose only row is numbered 1048577:
//
//	>>> pandas.read_excel(io.BytesIO(data), engine='openpyxl').columns.tolist()
//	['Nome', 'CAP']                       # header at row 1
//	['Unnamed: 0', 'Unnamed: 1']          # no header row
//	['Unnamed: 0']                        # a single cell at row 1048578
//	ValueError: invalid literal for int() with base 10: 'abc'   # a bad number cell
func TestReadColumns(t *testing.T) {
	header := `<row r="1"><c r="A1" t="inlineStr"><is><t>Nome</t></is></c>` +
		`<c r="B1" t="inlineStr"><is><t>CAP</t></is></c></row>`
	huge := `<row r="1048577"><c r="A1048577" t="inlineStr"><is><t>Mario</t></is></c><c r="B1048577"><v>100</v></c></row>`
	for _, tc := range []struct {
		name    string
		sheet   string
		columns []string
	}{
		{"header and a row at 1048577", header + huge, []string{"Nome", "CAP"}},
		{"only a row at 1048577", huge, []string{"Unnamed: 0", "Unnamed: 1"}},
		{"only a row at 1048578", `<row r="1048578"><c r="A1048578" t="inlineStr"><is><t>Mario</t></is></c></row>`,
			[]string{"Unnamed: 0"}},
	} {
		data := testWorkbook(t, sheetOf(tc.sheet), stringsOf())
		if _, err := ReadAll(data, nil); !errors.Is(err, ErrRowsPastBound) {
			t.Errorf("%s: ReadAll = %v, want ErrRowsPastBound", tc.name, err)
		}
		frame, err := ReadColumns(data)
		if err != nil {
			t.Errorf("%s: %v", tc.name, err)
			continue
		}
		if len(frame.Columns) != len(tc.columns) {
			t.Errorf("%s: columns %v, want %v", tc.name, frame.Columns, tc.columns)
			continue
		}
		for index, column := range tc.columns {
			if frame.Columns[index] != column {
				t.Errorf("%s: column %d %q, want %q", tc.name, index, frame.Columns[index], column)
			}
		}
	}

	// A cell openpyxl raises on still raises, also past the bound.
	bad := `<row r="1048577"><c r="A1048577"><v>abc</v></c></row>`
	data := testWorkbook(t, sheetOf(header+bad), stringsOf())
	if _, err := ReadColumns(data); !errors.Is(err, ErrInvalid) {
		t.Errorf("a bad number cell past the bound: %v, want ErrInvalid", err)
	}

	// A row exactly at maxRowNumber is still a frame ReadAll builds (one row
	// per number, 1048576 rows), and ReadColumns answers the same columns.
	atBound := `<row r="1">` + `<c r="A1" t="inlineStr"><is><t>Nome</t></is></c></row>` +
		`<row r="1048576"><c r="A1048576" t="inlineStr"><is><t>Mario</t></is></c></row>`
	data = testWorkbook(t, sheetOf(atBound), stringsOf())
	frame, err := ReadAll(data, nil)
	if err != nil {
		t.Fatalf("a row at the bound: %v", err)
	}
	if len(frame.Rows) != maxRowNumber-1 {
		t.Errorf("a row at the bound: %d rows, want %d", len(frame.Rows), maxRowNumber-1)
	}
	narrow, err := ReadColumns(data)
	if err != nil || len(narrow.Columns) != 1 || narrow.Columns[0] != "Nome" {
		t.Errorf("a row at the bound: ReadColumns %v, %v", narrow, err)
	}

	// ReadColumns names the columns as ReadAll does for a workbook inside the
	// bound: a wider later row pads the header with empty names, and trailing
	// blank cells and rows are trimmed.
	ragged := `<row r="1"><c r="A1" t="inlineStr"><is><t>A</t></is></c><c r="B1" t="inlineStr"><is><t></t></is></c></row>` +
		`<row r="2"><c r="A2"><v>1</v></c><c r="B2"><v>2</v></c><c r="C2"><v>3</v></c></row>` +
		`<row r="3"><c r="A3" t="inlineStr"><is><t>x</t></is></c><c r="B3" t="inlineStr"><is><t> </t></is></c></row>`
	data = testWorkbook(t, sheetOf(ragged), stringsOf())
	full, err := ReadAll(data, nil)
	if err != nil {
		t.Fatal(err)
	}
	narrow, err = ReadColumns(data)
	if err != nil {
		t.Fatal(err)
	}
	if len(narrow.Columns) != len(full.Columns) {
		t.Fatalf("columns %v, want %v", narrow.Columns, full.Columns)
	}
	for index := range full.Columns {
		if narrow.Columns[index] != full.Columns[index] || narrow.Labels[index].Kind != full.Labels[index].Kind {
			t.Errorf("column %d: %q %v, want %q %v", index, narrow.Columns[index], narrow.Labels[index].Kind,
				full.Columns[index], full.Labels[index].Kind)
		}
	}
}
