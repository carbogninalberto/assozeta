package pandasxlsx

import (
	"testing"
)

// durationStyles is a stylesheet whose second cell format is the builtin
// [h]:mm:ss (46): a number under it is a duration (openpyxl's from_excel with
// timedelta=True).
const durationStyles = `<?xml version="1.0" encoding="UTF-8"?>` +
	`<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">` +
	`<cellXfs count="2"><xf numFmtId="0"/><xf numFmtId="46" applyNumberFormat="1"/></cellXfs></styleSheet>`

// durationWorkbook is a one-sheet workbook whose s="1" cells are durations.
func durationWorkbook(t *testing.T, body string) []byte {
	t.Helper()
	return zipParts(t, append(testParts(sheetOf(body), stringsOf(`<si><t>B</t></si>`)),
		[2]string{"xl/styles.xml", durationStyles}))
}

// durationHeader is one row of duration cells with the text label 'B' after
// them, and one row of values.
func durationHeader(values ...string) string {
	body := `<row r="1">`
	for index, value := range values {
		body += `<c r="` + string(rune('A'+index)) + `1" s="1"><v>` + value + `</v></c>`
	}
	body += `<c r="` + string(rune('A'+len(values))) + `1" t="s"><v>0</v></c></row>` +
		`<row r="2"><c r="A2"><v>1</v></c><c r="B2"><v>2</v></c></row>`
	return body
}

// TestDurationLabelPastTimedelta is a header cell that is a duration past
// pandas' Timedelta range and inside Python's datetime.timedelta range
// (Timedelta.max is 106751 days 23:47:16.854775807, datetime.timedelta's last
// day is 999999999): pandas keeps it as a datetime.timedelta object, which
// makes the column Index an object Index, names the column str(timedelta),
// de-duplicates it by that str() and renders it as str(total_seconds()) (DRF's
// JSONEncoder).
//
// Expected values: Python 3.13.16, pandas 2.2.3, openpyxl 3.1.5, DRF 3.17.2,
// on this exact workbook (scratch/w3/probe_gowb.py):
//
//	>>> frame = pandas.read_excel(io.BytesIO(data), engine='openpyxl')
//	>>> frame.columns.tolist()
//	[datetime.timedelta(days=200000), 'B']
//	>>> frame.columns.dtype
//	dtype('O')
//	>>> json.dumps(frame.columns.tolist(), cls=JSONEncoder)
//	'["17280000000.0", "B"]'
//	>>> # -200000.5: [datetime.timedelta(days=-200001, seconds=43200), 'B'], '-17280043200.0'
//	>>> # 999999999: [datetime.timedelta(days=999999999), 'B'], '86399999913600.0'
//	>>> # 999999998.999999: '999999998 days, 23:59:59.918000', '86399999913599.92'
//	>>> # 106751.999999 (inside Timedelta's range; the object Index keeps it a
//	>>> # datetime.timedelta): '106751 days, 23:59:59.914000', '9223372799.914'
//	>>> # 1000000000 (past datetime.timedelta's last day): openpyxl answers the
//	>>> # #VALUE! error cell, so the label is nan (and DRF writes a bare NaN).
//	>>> # two 200000 cells: [datetime.timedelta(days=200000), '200000 days, 0:00:00.1']
func TestDurationLabelPastTimedelta(t *testing.T) {
	for _, tc := range []struct {
		value   string
		label   string // str() of the label
		seconds string // str(label.total_seconds())
	}{
		{"200000", "200000 days, 0:00:00", "17280000000.0"},
		{"-200000.5", "-200001 days, 12:00:00", "-17280043200.0"},
		{"999999999", "999999999 days, 0:00:00", "86399999913600.0"},
		{"999999998.999999", "999999998 days, 23:59:59.918000", "86399999913599.92"},
		{"106751.999999", "106751 days, 23:59:59.914000", "9223372799.914"},
	} {
		frame, err := ReadAll(durationWorkbook(t, durationHeader(tc.value)), nil)
		if err != nil {
			t.Errorf("%s days: %v", tc.value, err)
			continue
		}
		if len(frame.Labels) != 2 || len(frame.Columns) != 2 || frame.Index != ObjectIndex {
			t.Errorf("%s days: labels %v, index %v", tc.value, frame.Columns, frame.Index)
			continue
		}
		label := frame.Labels[0]
		if label.Kind != PyTimedelta || label.Str() != tc.label || frame.Columns[0] != tc.label {
			t.Errorf("%s days: label %v %q (column %q), want PyTimedelta %q", tc.value, label.Kind, label.Str(),
				frame.Columns[0], tc.label)
		}
		if got := floatRepr(label.TotalSeconds()); got != tc.seconds {
			t.Errorf("%s days: total_seconds %s, want %s", tc.value, got, tc.seconds)
		}
		// The labels are the timedelta and 'B': a text is no label for the
		// timedelta, and 'B' is the second column.
		if position, found, raises, err := frame.Lookup(tc.label); err != nil || found || raises {
			t.Errorf("%s days: lookup %q = %d, %v, %v, %v; want absent", tc.value, tc.label, position, found, raises, err)
		}
		if position, found, raises, err := frame.Lookup("B"); err != nil || !found || raises || position != 1 {
			t.Errorf("%s days: lookup B = %d, %v, %v, %v; want column 1", tc.value, position, found, raises, err)
		}
	}

	// Two equal durations: the parser's de-duplication suffixes str() of the
	// label, so the second column is a text label.
	frame, err := ReadAll(durationWorkbook(t, durationHeader("200000", "200000")), nil)
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"200000 days, 0:00:00", "200000 days, 0:00:00.1", "B"}
	if len(frame.Columns) != len(want) {
		t.Fatalf("columns %v, want %v", frame.Columns, want)
	}
	for index, column := range want {
		if frame.Columns[index] != column {
			t.Errorf("column %d: %q, want %q", index, frame.Columns[index], column)
		}
	}
	if frame.Labels[0].Kind != PyTimedelta || frame.Labels[1].Kind != String {
		t.Errorf("de-duplicated labels are %v and %v, want PyTimedelta and str", frame.Labels[0].Kind, frame.Labels[1].Kind)
	}

	// Past datetime.timedelta's last day openpyxl answers the #VALUE! error
	// cell: the label is nan, which the response renderer cannot write (the
	// same 500 every error-cell header answers).
	frame, err = ReadAll(durationWorkbook(t, durationHeader("1000000000")), nil)
	if err != nil {
		t.Fatal(err)
	}
	if frame.Labels[0].Kind != NaN {
		t.Errorf("a day count past timedelta's range is %v, want nan", frame.Labels[0].Kind)
	}
}

// TestTotalSeconds checks the text DRF's JSONEncoder writes for a duration
// label: str(total_seconds()). Expected values: Python 3.13.16 (datetime's
// timedelta and pandas 2.2.3's Timedelta):
//
//	>>> datetime.timedelta(microseconds=17280000000000000).total_seconds()
//	17280000000.0
//	>>> [repr(datetime.timedelta(microseconds=v).total_seconds()) for v in
//	...  (-17280043200000000, 86399999913600000000, 86399999913599918000,
//	...   9223372799914000, -8531699129511414324, -1500)]
//	['-17280043200.0', '86399999913600.0', '86399999913599.92', '9223372799.914',
//	 '-8531699129511.414', '-0.0015']
//
// pandas' Timedelta.total_seconds() reads the same for the millisecond-
// precision values openpyxl's duration cells make (a million values checked in
// scratch/w3/probe_round2.py); the division is done in exact rationals so that
// no intermediate double rounds twice.
func TestTotalSeconds(t *testing.T) {
	for _, tc := range []struct {
		value Value
		want  string
	}{
		{Value{Kind: PyTimedelta, Micro: mustInt("17280000000000000")}, "17280000000.0"},
		{Value{Kind: PyTimedelta, Micro: mustInt("-17280043200000000")}, "-17280043200.0"},
		{Value{Kind: PyTimedelta, Micro: mustInt("86399999913600000000")}, "86399999913600.0"},
		{Value{Kind: PyTimedelta, Micro: mustInt("86399999913599918000")}, "86399999913599.92"},
		{Value{Kind: PyTimedelta, Micro: mustInt("9223372799914000")}, "9223372799.914"},
		{Value{Kind: PyTimedelta, Micro: mustInt("-8531699129511414324")}, "-8531699129511.414"},
		{Value{Kind: PyTimedelta, Micro: mustInt("-1500")}, "-0.0015"},
		{Value{Kind: Timedelta, D: 129600000000000}, "129600.0"},
		{Value{Kind: Timedelta, D: 1234567890123456789}, "1234567890.123456"},
		{Value{Kind: Timedelta, D: 9223372036854775807}, "9223372036.854774"}, // the nanoseconds do not show
	} {
		if got := floatRepr(tc.value.TotalSeconds()); got != tc.want {
			t.Errorf("%v: %s, want %s", tc.value, got, tc.want)
		}
	}
}
