package service

import (
	"encoding/json"
	"math"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/xlsxexport"
)

var reportLabelsNow = time.Date(2026, time.November, 17, 9, 0, 0, 0, time.UTC)

// reportLabelsData is one Tags row as the query serializer hands it to the
// exporter: two text columns.
func reportLabelsData() []any {
	return []any{domain.RequestObject{
		{Name: "tag_name", Value: "Nuoto"},
		{Name: "creation_date", Value: "15/09/2026 08:30"},
	}}
}

// reportLabelsSheet is mcpReportSheet's workbook of that row with labels, and
// its worksheet XML.
func reportLabelsSheet(t *testing.T, labels domain.RequestObject) (xlsxexport.ReportSheet, string) {
	t.Helper()
	sheet, err := mcpReportSheet("Tags", reportLabelsData(), labels)
	if err != nil {
		t.Fatal(err)
	}
	workbook, err := xlsxexport.ReportWorkbook(sheet, reportLabelsNow)
	if err != nil {
		t.Fatal(err)
	}
	parts, err := xlsxexport.Parts(workbook)
	if err != nil {
		t.Fatal(err)
	}
	for _, part := range parts {
		if part.Name == "xl/worksheets/sheet1.xml" {
			return sheet, string(part.Data)
		}
	}
	t.Fatal("no sheet1.xml")
	return sheet, ""
}

// labelsOf pairs column names with label values in column order.
func labelsOf(pairs ...any) domain.RequestObject {
	labels := domain.RequestObject{}
	for i := 0; i < len(pairs); i += 2 {
		labels = append(labels, domain.RequestField{Name: pairs[i].(string), Value: pairs[i+1]})
	}
	return labels
}

func containsAll(t *testing.T, subject string, wanted ...string) {
	t.Helper()
	for _, want := range wanted {
		if !strings.Contains(subject, want) {
			t.Fatalf("workbook %s does not hold %s", subject, want)
		}
	}
}

// A column label that is a JSON float beyond the double range (1e400 in the
// run's overrides) is a float infinity: pandas writes the header 'inf' (or
// '-inf') as a str, so the run answers 200 with that workbook.
func TestReportSheetLabelInfinity(t *testing.T) {
	for _, tc := range []struct {
		name, number, header string
		sign                 int
	}{
		{"positive", "1e400", "inf", 1},
		{"negative", "-1e400", "-inf", -1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			sheet, xml := reportLabelsSheet(t, labelsOf("tag_name", json.Number(tc.number), "creation_date", "Creato"))
			if label := sheet.Labels[0]; label.Kind != xlsxexport.Float || !math.IsInf(label.Float, tc.sign) {
				t.Fatalf("label %+v", label)
			}
			if sheet.Labels[1] != xlsxexport.StrValue("Creato") {
				t.Fatalf("label %+v", sheet.Labels[1])
			}
			if sheet.Columns[0] != tc.header || sheet.Columns[1] != "Creato" {
				t.Fatalf("columns %q", sheet.Columns)
			}
			containsAll(t, xml,
				`<c r="A1" s="1" t="inlineStr"><is><t>`+tc.header+`</t></is></c>`,
				`<c r="B1" s="1" t="inlineStr"><is><t>Creato</t></is></c>`,
				`<col width="7" customWidth="1" min="1" max="1" />`,
				`<col width="18" customWidth="1" min="2" max="2" />`)
		})
	}
}

// The agent's tool arguments carry NaN, Infinity and -Infinity as float64
// (json.loads accepts them). A NaN header is the empty string pandas writes
// for na_rep; a number label in the float index still writes its number cell.
func TestReportSheetLabelAgentNaN(t *testing.T) {
	sheet, xml := reportLabelsSheet(t, labelsOf("tag_name", math.NaN(), "creation_date", json.Number("5")))
	if label := sheet.Labels[0]; label.Kind != xlsxexport.Float || !math.IsNaN(label.Float) {
		t.Fatalf("label %+v", label)
	}
	if label := sheet.Labels[1]; label.Kind != xlsxexport.Int || label.Int != 5 {
		t.Fatalf("label %+v", label)
	}
	if sheet.Columns[0] != "nan" || sheet.Columns[1] != "5.0" {
		t.Fatalf("columns %q", sheet.Columns)
	}
	containsAll(t, xml,
		`<c r="A1" s="1" t="inlineStr" />`,
		`<c r="B1" s="1" t="n"><v>5</v></c>`,
		`<col width="7" customWidth="1" min="1" max="1" />`,
		`<col width="18" customWidth="1" min="2" max="2" />`)
}

// An Infinity label beside a None: a float index writes the infinity as the
// str 'inf' and the None as the empty header.
func TestReportSheetLabelAgentInfinity(t *testing.T) {
	sheet, xml := reportLabelsSheet(t, labelsOf("tag_name", math.Inf(1), "creation_date", nil))
	if label := sheet.Labels[0]; label.Kind != xlsxexport.Float || !math.IsInf(label.Float, 1) {
		t.Fatalf("label %+v", label)
	}
	if sheet.Labels[1].Kind != xlsxexport.None {
		t.Fatalf("label %+v", sheet.Labels[1])
	}
	if sheet.Columns[0] != "inf" || sheet.Columns[1] != "nan" {
		t.Fatalf("columns %q", sheet.Columns)
	}
	containsAll(t, xml,
		`<c r="A1" s="1" t="inlineStr"><is><t>inf</t></is></c>`,
		`<c r="B1" s="1" t="inlineStr" />`,
		`<col width="7" customWidth="1" min="1" max="1" />`,
		`<col width="18" customWidth="1" min="2" max="2" />`)
}

// Two NaN labels (a None and a NaN, or two NaNs) select the same pandas
// column: get_loc(nan) matches both, so pandas measures the column from the
// frame shape (len(str(col)) = 3 for 'nan': 5 with one row).
func TestReportSheetLabelsNaNSelectOneColumn(t *testing.T) {
	for _, tc := range []struct {
		name          string
		first, second any
	}{
		{"none-and-nan", nil, math.NaN()},
		{"two-nans", math.NaN(), math.NaN()},
	} {
		t.Run(tc.name, func(t *testing.T) {
			sheet, xml := reportLabelsSheet(t, labelsOf("tag_name", tc.first, "creation_date", tc.second))
			if sheet.Columns[0] != "nan" || sheet.Columns[1] != "nan" {
				t.Fatalf("columns %q", sheet.Columns)
			}
			containsAll(t, xml,
				`<c r="A1" s="1" t="inlineStr" />`,
				`<c r="B1" s="1" t="inlineStr" />`,
				`<col width="5" customWidth="1" min="1" max="1" />`,
				`<col width="5" customWidth="1" min="2" max="2" />`)
		})
	}
}

// A finite float64 label beside an int in the same float index keeps both as
// number headers; the float index writes the int as '2.0' for the width.
func TestReportSheetLabelFiniteFloat(t *testing.T) {
	sheet, xml := reportLabelsSheet(t, labelsOf("tag_name", 1.5, "creation_date", json.Number("2")))
	if label := sheet.Labels[0]; label.Kind != xlsxexport.Float || label.Float != 1.5 {
		t.Fatalf("label %+v", label)
	}
	if sheet.Columns[0] != "1.5" || sheet.Columns[1] != "2.0" {
		t.Fatalf("columns %q", sheet.Columns)
	}
	containsAll(t, xml,
		`<c r="A1" s="1" t="n"><v>1.5</v></c>`,
		`<c r="B1" s="1" t="n"><v>2</v></c>`,
		`<col width="7" customWidth="1" min="1" max="1" />`,
		`<col width="18" customWidth="1" min="2" max="2" />`)
}
