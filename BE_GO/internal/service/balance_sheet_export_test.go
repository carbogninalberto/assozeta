package service

import (
	"encoding/json"
	"errors"
	"math"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/pdfexport"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/xlsxexport"
)

// A balance sheet description is any json.loads value: export_balance_sheet
// appends it to the Descrizione column as it is.
func TestBalanceSheetCellKinds(t *testing.T) {
	list := &pyList{items: []any{json.Number("1"), "due"}}
	dict := &pyDict{fields: []pyField{{"k", json.Number("1")}}}
	for _, test := range []struct {
		value any
		want  xlsxexport.Value
	}{
		{nil, xlsxexport.NoneValue()},
		{"Quote è", xlsxexport.StrValue("Quote è")},
		{true, xlsxexport.BoolValue(true)},
		{json.Number("5"), xlsxexport.IntValue(5)},
		// jsonb keeps a Python int of any size: pandas writes its float.
		{json.Number("9223372036854775808"), xlsxexport.BigIntValue("9223372036854775808")},
		{json.Number("1" + strings.Repeat("0", 400)), xlsxexport.BigIntValue("1" + strings.Repeat("0", 400))},
		{1.5, xlsxexport.FloatValue(1.5)},
		// json.loads of 1e999 is inf.
		{math.Inf(1), xlsxexport.FloatValue(math.Inf(1))},
		{list, xlsxexport.ObjectValue("[1, 'due']")},
		{dict, xlsxexport.ObjectValue("{'k': 1}")},
	} {
		got, ok := balanceSheetCell(test.value)
		if !ok {
			t.Fatalf("balanceSheetCell(%v): unexpected", test.value)
		}
		if got != test.want {
			t.Errorf("balanceSheetCell(%v) = %+v, want %+v", test.value, got, test.want)
		}
	}
}

// reportlab draws str(value); a non-empty list is taken for a list of flowables,
// which is an exception of the export (the view's 500 page, not a refusal).
func TestBalanceSheetCellReportlabRows(t *testing.T) {
	dict := &pyDict{fields: []pyField{{"k", json.Number("1")}}}
	dictCell, _ := balanceSheetCell(dict)
	if cell := pdfexport.CellOf(dictCell); cell.Text != "{'k': 1}" || cell.Sequence {
		t.Fatalf("dict cell = %+v", cell)
	}
	bigCell, _ := balanceSheetCell(json.Number("9223372036854775808"))
	if cell := pdfexport.CellOf(bigCell); cell.Text != "9223372036854775808" {
		t.Fatalf("big int cell = %+v", cell)
	}
	list := &pyList{items: []any{"a"}}
	listCell, _ := balanceSheetCell(list)
	if _, err := (pdfexport.DataTable{Columns: []string{"Descrizione"}, Rows: pdfexport.RowsOf([][]xlsxexport.Value{{listCell}})}).
		Build(printingTestNow); !errors.Is(err, pdfexport.ErrLayout) {
		t.Fatalf("a list description: %v, want the AttributeError of the export", err)
	}
}

// The workbook of the four kinds get_excel_base64 writes: the ints and floats as
// numbers, the bool as a bool, the list and dict as their repr, the enormous int
// failing the workbook (get_excel_base64 returns None).
func TestBalanceSheetCellWorkbook(t *testing.T) {
	values := make([]xlsxexport.Value, 0, 5)
	for _, value := range []any{json.Number("5"), 1.5, true, &pyList{items: []any{json.Number("1"), "due"}},
		&pyDict{fields: []pyField{{"k", json.Number("1")}}}} {
		cell, ok := balanceSheetCell(value)
		if !ok {
			t.Fatalf("balanceSheetCell(%v): unexpected", value)
		}
		values = append(values, cell)
	}
	workbook, err := xlsxexport.PandasOpenpyxl([][]xlsxexport.Value{values}, []string{"Descrizione"}, printingTestNow)
	if err != nil {
		t.Fatal(err)
	}
	parts, err := xlsxexport.Parts(workbook)
	if err != nil {
		t.Fatal(err)
	}
	for _, part := range parts {
		if part.Name != "xl/worksheets/sheet1.xml" {
			continue
		}
		sheet := string(part.Data)
		for _, want := range []string{`<v>5</v>`, `<v>1.5</v>`, `<v>1</v>`, `<t>[1, 'due']</t>`, `<t>{'k': 1}</t>`} {
			if !strings.Contains(sheet, want) {
				t.Fatalf("sheet %s does not hold %s", sheet, want)
			}
		}
		if _, err := xlsxexport.PandasOpenpyxl([][]xlsxexport.Value{{xlsxexport.BigIntValue("1" + strings.Repeat("0", 400))}},
			[]string{"Descrizione"}, printingTestNow); !errors.Is(err, xlsxexport.ErrDjango) {
			t.Fatalf("an int too large for float: %v, want ErrDjango", err)
		}
		return
	}
	t.Fatal("no sheet")
}
