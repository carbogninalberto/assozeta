package service

import (
	"encoding/json"
	"errors"
	"math"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/pdfexport"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/xlsxexport"
)

var printingTestNow = time.Date(2026, 11, 16, 9, 0, 0, 0, time.UTC)

// A stored JSONField value is a json.loads output: an int of any size, a float
// (inf past the double range), a bool, a list or a dict.
func TestPrintingCellKinds(t *testing.T) {
	for _, test := range []struct {
		value any
		want  xlsxexport.Value
	}{
		{nil, xlsxexport.NoneValue()},
		{"Iscrizione", xlsxexport.StrValue("Iscrizione")},
		{true, xlsxexport.BoolValue(true)},
		{json.Number("12"), xlsxexport.IntValue(12)},
		{json.Number("9223372036854775807"), xlsxexport.IntValue(math.MaxInt64)},
		// json.loads keeps a Python int past int64: pandas writes its float.
		{json.Number("9223372036854775808"), xlsxexport.BigIntValue("9223372036854775808")},
		{json.Number("-9223372036854775809"), xlsxexport.BigIntValue("-9223372036854775809")},
		{json.Number("1.5"), xlsxexport.FloatValue(1.5)},
		{json.Number("1e400"), xlsxexport.FloatValue(math.Inf(1))},
		{[]any{json.Number("1"), "x"}, xlsxexport.ObjectValue("[1, 'x']")},
		{domain.RequestObject{{Name: "k", Value: json.Number("1")}}, xlsxexport.ObjectValue("{'k': 1}")},
	} {
		got, err := printingCell(test.value)
		if err != nil {
			t.Fatalf("printingCell(%v): %v", test.value, err)
		}
		if got != test.want {
			t.Errorf("printingCell(%v) = %+v, want %+v", test.value, got, test.want)
		}
	}
}

// reportlab draws str(value): a big int's digits, and a non-empty list is taken
// for a list of flowables (an exception of the export).
func TestPrintingCellReportlabRows(t *testing.T) {
	big, err := printingCell(json.Number("9223372036854775808"))
	if err != nil {
		t.Fatal(err)
	}
	if rows := pdfexport.RowsOf([][]xlsxexport.Value{{big}}); rows[0][0].Text != "9223372036854775808" || rows[0][0].None {
		t.Fatalf("rows = %+v", rows)
	}
	list, err := printingCell([]any{"a"})
	if err != nil {
		t.Fatal(err)
	}
	rows := pdfexport.RowsOf([][]xlsxexport.Value{{list}})
	if !rows[0][0].Sequence || rows[0][0].Empty {
		t.Fatalf("rows = %+v", rows)
	}
	if _, err := (pdfexport.DataTable{Columns: []string{"c"}, Rows: rows}).Build(printingTestNow); !errors.Is(err, pdfexport.ErrLayout) {
		t.Fatalf("a list cell: %v, want the AttributeError of the export", err)
	}
}

// The mapping is the one get_excel_base64 applies: the big int's float in a
// numeric cell, its digits as the column width.
func TestPrintingCellWorkbook(t *testing.T) {
	big, err := printingCell(json.Number("9223372036854775808"))
	if err != nil {
		t.Fatal(err)
	}
	workbook, err := xlsxexport.PandasOpenpyxl([][]xlsxexport.Value{{big, xlsxexport.StrValue("Iscrizione")}}, []string{"corso/carnet"}, printingTestNow)
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
		for _, want := range []string{"<v>9.223372036854776e+18</v>", `<col width="21"`} {
			if !strings.Contains(sheet, want) {
				t.Fatalf("sheet %s does not hold %s", sheet, want)
			}
		}
		return
	}
	t.Fatal("no sheet")
}
