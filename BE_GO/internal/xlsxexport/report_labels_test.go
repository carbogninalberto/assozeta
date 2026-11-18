package xlsxexport

import (
	"math"
	"strings"
	"testing"
	"time"
)

// typedLabels of the non-finite floats: pandas formats a NaN header with
// na_rep (the empty string, as the None label writes) and an infinity with
// inf_rep ('inf', '-inf') as a str, so the header cells are the ones a str
// label of that text writes.
func TestTypedLabelsNonFiniteFloats(t *testing.T) {
	labels := []Value{FloatValue(math.NaN()), FloatValue(math.Inf(1)), FloatValue(math.Inf(-1))}
	sheet := &opSheet{rows: []opRow{{row: 1, cells: make([]opCell, len(labels))}}}
	if err := typedLabels(sheet, labels); err != nil {
		t.Fatal(err)
	}
	want := []opCell{
		{col: 1, kind: 's', styled: true},
		{col: 2, kind: 's', text: "inf", styled: true},
		{col: 3, kind: 's', text: "-inf", styled: true},
	}
	for i, cell := range sheet.rows[0].cells {
		if cell != want[i] {
			t.Fatalf("header cell %d = %+v, want %+v", i, cell, want[i])
		}
	}
}

// LabelTexts and labelKeys of NaN/±Inf: a float index writes the labels as
// str() does ('nan', 'inf', '-inf') for the widths, and every NaN (a None in
// a float index too) is the column get_loc(nan) selects.
func TestLabelTextsNonFiniteFloats(t *testing.T) {
	labels := []Value{FloatValue(math.NaN()), FloatValue(math.Inf(1)), FloatValue(math.Inf(-1)), NoneValue(), IntValue(2)}
	if dtype := labelDtype(labels); dtype != labelFloat {
		t.Fatalf("dtype %v, want a float index", dtype)
	}
	wantTexts := []string{"nan", "inf", "-inf", "nan", "2.0"}
	if texts := LabelTexts(labels); strings.Join(texts, "|") != strings.Join(wantTexts, "|") {
		t.Fatalf("LabelTexts = %q, want %q", texts, wantTexts)
	}
	columns := []string{"a", "b", "c", "d", "e"}
	wantKeys := []string{"f:nan", "f:inf", "f:-inf", "f:nan", "n:2"}
	if keys := labelKeys(labels, columns); strings.Join(keys, "|") != strings.Join(wantKeys, "|") {
		t.Fatalf("labelKeys = %q, want %q", keys, wantKeys)
	}
}

// The header row of a report sheet whose labels are NaN, +Inf and -Inf: the
// empty inline string and the inf texts, with the str label widths
// str(nan)/str(inf)/str(-inf).
func TestReportWorkbookNonFiniteLabels(t *testing.T) {
	now := time.Date(2026, time.November, 17, 9, 0, 0, 0, time.UTC)
	labels := []Value{FloatValue(math.NaN()), FloatValue(math.Inf(1)), FloatValue(math.Inf(-1)), StrValue("testo")}
	sheet := ReportSheet{
		Title:   "T",
		Labels:  labels,
		Columns: LabelTexts(labels),
		Data:    [][]Value{{StrValue("x")}, {StrValue("y")}, {StrValue("z")}, {StrValue("w")}},
	}
	workbook, err := ReportWorkbook(sheet, now)
	if err != nil {
		t.Fatal(err)
	}
	parts, err := Parts(workbook)
	if err != nil {
		t.Fatal(err)
	}
	sheetXML := ""
	for _, part := range parts {
		if part.Name == "xl/worksheets/sheet1.xml" {
			sheetXML = string(part.Data)
		}
	}
	for _, want := range []string{
		`<c r="A1" s="1" t="inlineStr" />`,
		`<c r="B1" s="1" t="inlineStr"><is><t>inf</t></is></c>`,
		`<c r="C1" s="1" t="inlineStr"><is><t>-inf</t></is></c>`,
		`<c r="D1" s="1" t="inlineStr"><is><t>testo</t></is></c>`,
		`<col width="5" customWidth="1" min="1" max="1" />`,
		`<col width="5" customWidth="1" min="2" max="2" />`,
		`<col width="6" customWidth="1" min="3" max="3" />`,
		`<col width="7" customWidth="1" min="4" max="4" />`,
	} {
		if !strings.Contains(sheetXML, want) {
			t.Fatalf("sheet %s does not hold %s", sheetXML, want)
		}
	}
}
