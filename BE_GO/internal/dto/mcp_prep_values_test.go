package dto

import (
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"math/big"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

func mcpValueSettings() (ReportSettings, error) {
	return ReportSettings{MaxResults: 5000, QueryTimeout: 10}, nil
}

// mcpCountPlan plans count_data for a filter object built by the test.
func mcpCountPlan(t *testing.T, model, key string, value any) (ReportPlan, error) {
	t.Helper()
	params := domain.RequestObject{
		{Name: "model_name", Value: model},
		{Name: "filters", Value: domain.RequestObject{{Name: key, Value: value}}},
	}
	return PlanReportTool("count_data", params, "a1000000-0000-4000-8000-000000701001", mcpValueSettings)
}

func mcpCountPlanJSON(t *testing.T, text string) (ReportPlan, error) {
	t.Helper()
	return PlanReportTool("count_data", reportParams(t, text), "a1000000-0000-4000-8000-000000701001", mcpValueSettings)
}

func mcpPlannedArgs(plan ReportPlan) []any { return plan.Statements[0].Args }

func mcpHasArg(args []any, want any) bool {
	for _, argument := range args {
		if fmt.Sprint(argument) == fmt.Sprint(want) {
			return true
		}
		if got, ok := argument.(float64); ok {
			if expected, isFloat := want.(float64); isFloat && math.IsNaN(got) && math.IsNaN(expected) {
				return true
			}
		}
	}
	return false
}

func TestMCPStrptimeUnicodeDigits(t *testing.T) {
	for _, tc := range []struct {
		text, want string
		isDate     bool
	}{
		{"٢025-03-05", "2025-03-05", true},
		{"٢٠٢٥-03-05T08:00:00", "2025-03-05 08:00:00", false},
		{"2025-03-05\u00a008:00:00", "2025-03-05 08:00:00", false},
		{"2025-03-05\u2028\u202808:00:00", "2025-03-05 08:00:00", false},
	} {
		switch typed := mcpCoerceDate(tc.text).(type) {
		case mcpCoercedDate:
			if !tc.isDate || typed.t.Format("2006-01-02") != tc.want {
				t.Errorf("%q: got date %v, want %q (date %v)", tc.text, typed, tc.want, tc.isDate)
			}
		case mcpCoercedDateTime:
			if tc.isDate || typed.t.Format("2006-01-02 15:04:05") != tc.want {
				t.Errorf("%q: got datetime %v, want %q (date %v)", tc.text, typed, tc.want, tc.isDate)
			}
		default:
			t.Errorf("%q: got %T, want a parsed value", tc.text, typed)
		}
	}
	for _, text := range []string{"2025-٠٣-05", "2025-03-05T٠8:00:00"} {
		if parsed := mcpCoerceDate(text); parsed != text {
			t.Errorf("%q: got %#v, want it unchanged", text, parsed)
		}
	}
	if value, ok, uncertain := MCPStrptimeDate("٢025-03-05"); !ok || uncertain || value != "2025-03-05" {
		t.Errorf("MCPStrptimeDate: %q %v %v", value, ok, uncertain)
	}
	if _, ok, uncertain := MCPStrptimeDate("2025-٠٣-05"); ok || uncertain {
		t.Errorf("MCPStrptimeDate non-ASCII month: %v %v", ok, uncertain)
	}
}

func TestMCPRawArgumentValues(t *testing.T) {
	bigInt, _ := new(big.Int).SetString("99999999999999999999", 10)
	day := time.Date(2025, 3, 5, 0, 0, 0, 0, time.UTC)
	moment := time.Date(2025, 9, 1, 8, 0, 0, 0, time.UTC)
	for _, tc := range []struct {
		name  string
		value any
		sql   string
		arg   any
	}{
		{"text", "x", "$1", "x"},
		{"empty list", []any{}, "$1", "{}"},
		{"int list", []any{json.Number("1")}, "$1::int[]", []int32{1}},
		{"float list", []any{json.Number("1.5")}, "$1::numeric[]", []string{"1.5"}},
		{"bool", true, "$1::boolean", true},
		{"int", json.Number("5"), "$1::integer", int32(5)},
		{"bigint", json.Number("4000000000"), "$1::bigint", int64(4000000000)},
		{"numeric", json.Number("99999999999999999999"), "$1::numeric", "99999999999999999999"},
		{"float", json.Number("1.5"), "$1::double precision", 1.5},
		{"float overflow", json.Number("1e400"), "$1::double precision", math.Inf(1)},
		{"negative infinity", math.Inf(-1), "$1::double precision", math.Inf(-1)},
		{"coerced int", mcpCoercedInt{big.NewInt(7)}, "$1::integer", int32(7)},
		{"coerced wide int", mcpCoercedInt{bigInt}, "$1::numeric", "99999999999999999999"},
		{"coerced float", mcpCoercedFloat{1.5}, "$1::double precision", 1.5},
		{"coerced date", mcpCoercedDate{day}, "$1::date", "2025-03-05"},
		{"coerced datetime", mcpCoercedDateTime{moment}, "$1::timestamp", "2025-09-01 08:00:00"},
	} {
		q := newMCPQuery(mcpModelByLabel("application.Course"))
		sql, err := q.rawArgument(tc.value)
		if err != nil {
			t.Errorf("%s: %v", tc.name, err)
			continue
		}
		if sql != tc.sql {
			t.Errorf("%s: sql %q, want %q", tc.name, sql, tc.sql)
		}
		args := q.args.values
		if len(args) != 1 || !mcpHasArg(args, tc.arg) {
			t.Errorf("%s: args %#v lack %#v", tc.name, args, tc.arg)
		}
	}
	q := newMCPQuery(mcpModelByLabel("application.Course"))
	if _, err := q.rawArgument(domain.RequestObject{{Name: "a", Value: json.Number("1")}}); err == nil ||
		err.Error() != "can't adapt type 'dict'" {
		t.Errorf("dict: %v", err)
	}
	if _, err := q.rawArgument(struct{}{}); !errors.Is(err, refusal.ErrAppState) {
		t.Errorf("unexpected type: %v", err)
	}
}

func TestMCPRawLookupPlan(t *testing.T) {
	for _, tc := range []struct {
		params string
		sql    []string
		arg    any
	}{
		{`{"model_name":"Course","filters":{"title__regex":5}}`, []string{"::text ~ $", "::integer"}, nil},
		{`{"model_name":"Course","filters":{"title__regex":99999999999999999999}}`, []string{"::text ~ $", "::numeric"}, nil},
		{`{"model_name":"Course","filters":{"title__regex":[]}}`, []string{"::text ~ $"}, "{}"},
		{`{"model_name":"Course","filters":{"title__iexact":true}}`, []string{"UPPER(", "::boolean"}, nil},
		{`{"model_name":"Course","filters":{"title__iexact":[1]}}`, []string{"UPPER(", "::int[]"}, nil},
		{`{"model_name":"Course","filters":{"creation_date__regex":"2025-03-05"}}`, []string{"::text ~ $", "::date"}, nil},
		{`{"model_name":"Course","filters":{"start_date__regex":"2025-09-01T08:00:00"}}`,
			[]string{"::text ~ $", "::timestamp"}, nil},
	} {
		plan, err := mcpCountPlanJSON(t, tc.params)
		if err != nil {
			t.Errorf("%s: %v", tc.params, err)
			continue
		}
		query := plan.Statements[0].SQL
		for _, part := range tc.sql {
			if !strings.Contains(query, part) {
				t.Errorf("%s: query %q lacks %q", tc.params, query, part)
			}
		}
		if tc.arg != nil && !mcpHasArg(mcpPlannedArgs(plan), tc.arg) {
			t.Errorf("%s: args %#v lack %#v", tc.params, mcpPlannedArgs(plan), tc.arg)
		}
	}
	if _, err := mcpCountPlanJSON(t, `{"model_name":"Course","filters":{"title__iexact":{"a":1}}}`); err == nil ||
		err.Error() != "can't adapt type 'dict'" {
		t.Errorf("iexact dict: %v", err)
	}
}

func TestMCPPatternAndTextFloats(t *testing.T) {
	for _, tc := range []struct {
		name  string
		value any
		want  string
	}{
		{"overflow", json.Number("1e400"), "inf"},
		{"negative overflow", json.Number("-1e400"), "-inf"},
		{"float infinity", math.Inf(1), "inf"},
		{"float nan", math.NaN(), "nan"},
		{"coerced infinity", mcpCoercedFloat{math.Inf(1)}, "inf"},
		{"coerced finite", mcpCoercedFloat{1.5}, "1.5"},
		{"float finite", 1.5, "1.5"},
	} {
		text, err := mcpPatternText(tc.value)
		if err != nil || text != tc.want {
			t.Errorf("pattern %s: %q %v, want %q", tc.name, text, err, tc.want)
		}
	}
	if _, err := mcpPatternText(struct{}{}); !errors.Is(err, refusal.ErrAppState) {
		t.Errorf("pattern unexpected type: %v", err)
	}
	for _, tc := range []struct {
		name  string
		value any
		want  string
	}{
		{"overflow", json.Number("1e400"), "inf"},
		{"float infinity", math.Inf(1), "inf"},
		{"float nan", math.NaN(), "nan"},
		{"float finite", 1.5, "1.5"},
	} {
		text, err := mcpTextValue(tc.value)
		if err != nil || text != tc.want {
			t.Errorf("text %s: %q %v, want %q", tc.name, text, err, tc.want)
		}
	}
	if _, err := mcpTextValue(struct{}{}); !errors.Is(err, refusal.ErrAppState) {
		t.Errorf("text unexpected type: %v", err)
	}

	for _, tc := range []struct {
		params string
		arg    string
	}{
		{`{"model_name":"Course","filters":{"title__contains":1e400}}`, "%inf%"},
		{`{"model_name":"Course","filters":{"title":1e400}}`, "inf"},
	} {
		plan, err := mcpCountPlanJSON(t, tc.params)
		if err != nil {
			t.Errorf("%s: %v", tc.params, err)
			continue
		}
		if !mcpHasArg(mcpPlannedArgs(plan), tc.arg) {
			t.Errorf("%s: args %#v lack %q", tc.params, mcpPlannedArgs(plan), tc.arg)
		}
	}
	for _, tc := range []struct {
		key   string
		value float64
		want  string
	}{
		{"title__icontains", math.NaN(), "%nan%"},
		{"title__contains", math.Inf(-1), "%-inf%"},
		{"title", math.Inf(1), "inf"},
	} {
		plan, err := mcpCountPlan(t, "Course", tc.key, tc.value)
		if err != nil {
			t.Errorf("%s %v: %v", tc.key, tc.value, err)
			continue
		}
		if !mcpHasArg(mcpPlannedArgs(plan), tc.want) {
			t.Errorf("%s %v: args %#v lack %q", tc.key, tc.value, mcpPlannedArgs(plan), tc.want)
		}
	}
}

func TestMCPIntegerValues(t *testing.T) {
	for _, tc := range []struct {
		name  string
		value any
		want  string
	}{
		{"float truncates", json.Number("1.9"), "1"},
		{"coerced float truncates", mcpCoercedFloat{2.7}, "2"},
		{"string", "12", "12"},
	} {
		number, err := mcpIntValue(tc.value, "status_flag")
		if err != nil || number.String() != tc.want {
			t.Errorf("int %s: %v %v, want %s", tc.name, number, err, tc.want)
		}
	}
	for _, tc := range []struct {
		name  string
		value any
		want  string
	}{
		{"infinity", math.Inf(1), "cannot convert float infinity to integer"},
		{"overflow token", json.Number("1e400"), "cannot convert float infinity to integer"},
		{"coerced infinity", mcpCoercedFloat{math.Inf(-1)}, "cannot convert float infinity to integer"},
		{"nan", math.NaN(), "Field 'status_flag' expected a number but got nan."},
		{"coerced nan", mcpCoercedFloat{math.NaN()}, "Field 'status_flag' expected a number but got nan."},
	} {
		if _, err := mcpIntValue(tc.value, "status_flag"); err == nil || err.Error() != tc.want {
			t.Errorf("int %s: %v, want %q", tc.name, err, tc.want)
		}
	}
	if _, err := mcpIntValue(struct{}{}, "status_flag"); !errors.Is(err, refusal.ErrAppState) {
		t.Errorf("int unexpected type: %v", err)
	}

	for _, tc := range []struct {
		key   string
		value any
		want  string
	}{
		{"status_flag", math.Inf(1), "cannot convert float infinity to integer"},
		{"status_flag", math.NaN(), "Field 'status_flag' expected a number but got nan."},
		{"status_flag__gt", math.NaN(), "Field 'status_flag' expected a number but got nan."},
		{"status_flag__gte", math.Inf(1), "cannot convert float infinity to integer"},
		{"status_flag__lt", math.NaN(), "cannot convert float NaN to integer"},
		{"status_flag__in", []any{math.Inf(1)}, "cannot convert float infinity to integer"},
		{"status_flag__in", []any{math.NaN()}, "Field 'status_flag' expected a number but got nan."},
		{"status_flag", json.Number("1e400"), "cannot convert float infinity to integer"},
		{"start_date__year", "2025-03-05", "Field 'None' expected a number but got datetime.date(2025, 3, 5)."},
		{"start_date__month", "2025-03-05T08:00:00",
			"Field 'None' expected a number but got datetime.datetime(2025, 3, 5, 8, 0)."},
	} {
		if _, err := mcpCountPlan(t, "Course", tc.key, tc.value); err == nil || err.Error() != tc.want {
			t.Errorf("%s %v: %v, want %q", tc.key, tc.value, err, tc.want)
		}
	}
	plan, err := mcpCountPlan(t, "Course", "status_flag__gte", json.Number("1.2"))
	if err != nil {
		t.Fatalf("gte float: %v", err)
	}
	if !mcpHasArg(mcpPlannedArgs(plan), "2") {
		t.Errorf("gte float: args %#v lack 2", mcpPlannedArgs(plan))
	}
}

func TestMCPFloatValues(t *testing.T) {
	for _, tc := range []struct {
		name  string
		value any
		want  float64
		nan   bool
	}{
		{"float token", json.Number("1e400"), math.Inf(1), false},
		{"integer token", json.Number("5"), 5, false},
		{"float64", math.NaN(), 0, true},
		{"bool", true, 1, false},
		{"string", " 1.5 ", 1.5, false},
		{"infinite string", "inf", math.Inf(1), false},
		{"coerced infinity", mcpCoercedFloat{math.Inf(-1)}, math.Inf(-1), false},
	} {
		number, err := mcpFloatValue(tc.value, "score")
		if err != nil || (tc.nan && !math.IsNaN(number)) || (!tc.nan && number != tc.want) {
			t.Errorf("float %s: %v %v, want %v", tc.name, number, err, tc.want)
		}
	}
	huge := "1" + strings.Repeat("0", 400)
	if _, err := mcpFloatValue(json.Number(huge), "score"); err == nil ||
		err.Error() != "int too large to convert to float" {
		t.Errorf("huge int: %v", err)
	}
	for _, tc := range []struct {
		name  string
		value any
		want  string
	}{
		{"list", []any{json.Number("1")}, "Field 'score' expected a number but got [1]."},
		{"dict", domain.RequestObject{{Name: "a", Value: json.Number("1")}},
			"Field 'score' expected a number but got {'a': 1}."},
	} {
		if _, err := mcpFloatValue(tc.value, "score"); err == nil || err.Error() != tc.want {
			t.Errorf("float %s: %v, want %q", tc.name, err, tc.want)
		}
	}
	if _, err := mcpFloatValue(struct{}{}, "score"); !errors.Is(err, refusal.ErrAppState) {
		t.Errorf("float unexpected type: %v", err)
	}
}

func TestMCPBoolValues(t *testing.T) {
	for _, tc := range []struct {
		value any
		want  string
	}{
		{json.Number("1e400"), `['Il valore "inf" deve essere True oppure False.']`},
		{math.NaN(), `['Il valore "nan" deve essere True oppure False.']`},
	} {
		if _, err := mcpCountPlan(t, "Course", "pinned", tc.value); err == nil || err.Error() != tc.want {
			t.Errorf("bool %v: %v, want %q", tc.value, err, tc.want)
		}
	}
	plan, err := mcpCountPlan(t, "Course", "pinned", 1.0)
	if err != nil || !strings.Contains(plan.Statements[0].SQL, `"application_course"."pinned"`) ||
		strings.Contains(plan.Statements[0].SQL, `"pinned" =`) {
		t.Errorf("bool true: %v %q", err, plan.Statements[0].SQL)
	}
	plan, err = mcpCountPlan(t, "Course", "pinned", 0.0)
	if err != nil || !strings.Contains(plan.Statements[0].SQL, `NOT "application_course"."pinned"`) {
		t.Errorf("bool false: %v %q", err, plan.Statements[0].SQL)
	}
}

func TestMCPDecimalString(t *testing.T) {
	for _, tc := range []struct {
		text, want string
	}{
		{"1__0", "10"},
		{"_1", "1"},
		{"1_", "1"},
		{" 1 ", "1"},
		{"\u00a01\u00a0", "1"},
		{"\t1\n", "1"},
		{"١٢", "12"},
		{"١_٢", "12"},
		{"١٢.٣٤", "12.34"},
		{"1_0.5", "10.5"},
		{"1_.5", "1.5"},
		{"1._5", "1.5"},
		{"1.5_", "1.5"},
		{"1_e5", "1e5"},
		{"1e1_0", "1e10"},
		{"1e+_1", "1e1"},
		{".5", "0.5"},
		{"5.", "5"},
		{"+.5", "0.5"},
		{"1E+3", "1e3"},
		{"0001.500", "1.500"},
		{"-0", "-0"},
		{"1e999999999999999999", "1e999999999999999999"},
		{"1e-1999999999999999997", "1e-1999999999999999997"},
		{"0.0e999999999999999999", "0.0e999999999999999999"},
	} {
		got, ok := mcpDecimalString(tc.text)
		if !ok || got != tc.want {
			t.Errorf("decimal %q: %q %v, want %q", tc.text, got, ok, tc.want)
		}
	}
	for _, text := range []string{"1e", "_", "", ".", "e5", "1 2", "1,5", "abc", "Infinity", "NaN12",
		"-sNaN", "sNaN_1", "1e1000000000000000000", "1e-1999999999999999998", "١a", "1..5"} {
		if got, ok := mcpDecimalString(text); ok {
			t.Errorf("decimal %q: %q, want invalid", text, got)
		}
	}
}

func TestMCPDecimalValues(t *testing.T) {
	plan, err := mcpCountPlanJSON(t, `{"model_name":"Course","filters":{"fee__in":
		["1e1"," 45.50 ","4_5.5","+45.5",".5","5.","١٢","4.55e1"]}}`)
	if err != nil {
		t.Fatalf("fee in: %v", err)
	}
	for _, want := range []string{"1e1", "45.50", "45.5", "0.5", "5", "12", "4.55e1"} {
		if !mcpHasArg(mcpPlannedArgs(plan), want) {
			t.Errorf("fee in: args %#v lack %q", mcpPlannedArgs(plan), want)
		}
	}
	for _, tc := range []struct {
		params string
		want   string
	}{
		{`{"model_name":"Course","filters":{"fee__in":["Infinity"]}}`,
			`['Il valore "Infinity" deve essere un numero decimale.']`},
		{`{"model_name":"Course","filters":{"fee__in":["NaN"]}}`,
			`['Il valore "NaN" deve essere un numero decimale.']`},
		{`{"model_name":"Course","filters":{"fee__in":["abc"]}}`,
			`['Il valore "abc" deve essere un numero decimale.']`},
	} {
		if _, err := mcpCountPlanJSON(t, tc.params); err == nil || err.Error() != tc.want {
			t.Errorf("%s: %v, want %q", tc.params, err, tc.want)
		}
	}
	if plan, err := mcpCountPlanJSON(t, `{"model_name":"Course","filters":{"fee__in":["1e400"]}}`); err != nil ||
		!mcpHasArg(mcpPlannedArgs(plan), "1e400") {
		t.Errorf("fee 1e400: %v %#v", err, plan.Statements)
	}
	if _, err := mcpCountPlanJSON(t, `{"model_name":"Course","filters":{"fee":1e400}}`); err == nil ||
		err.Error() != `['Il valore "inf" deve essere un numero decimale.']` {
		t.Errorf("fee inf token: %v", err)
	}
	if _, err := mcpCountPlan(t, "Course", "fee", math.NaN()); err == nil ||
		err.Error() != `['Il valore "nan" deve essere un numero decimale.']` {
		t.Errorf("fee nan: %v", err)
	}
	plan, err = mcpCountPlanJSON(t, `{"model_name":"Course","filters":{"fee":45.5}}`)
	if err != nil || !mcpHasArg(mcpPlannedArgs(plan), "45.5") {
		t.Errorf("fee float: %v %#v", err, plan.Statements)
	}
	if value, err := mcpDecimalValue(mcpExpr{}, "1"); err != nil || value.param != "1" {
		t.Errorf("decimal string: %#v, %v", value, err)
	}
	if value, err := mcpDecimalValue(mcpExpr{}, json.Number("1")); err != nil || value.param != "1" {
		t.Errorf("decimal int: %#v, %v", value, err)
	}
	if _, err := mcpDecimalValue(mcpExpr{}, float64(1)); !errors.Is(err, refusal.ErrAppState) {
		t.Errorf("decimal float without max_digits: %v", err)
	}
	if _, err := mcpDecimalValue(mcpExpr{field: &mcpField{MaxDigits: 9}}, struct{}{}); !errors.Is(err, refusal.ErrAppState) {
		t.Errorf("decimal unexpected type: %v", err)
	}
}

func TestMCPTimeValues(t *testing.T) {
	moment := mcpCoercedDateTime{time.Date(2025, 9, 1, 8, 0, 0, 0, time.UTC)}
	for _, tc := range []struct {
		text string
		want string
	}{
		{"08:00:00+02:00", "08:00:00"},
		{"0800", "08:00:00"},
		{"T08:00", "08:00:00"},
		{"08:00:00.1234567", "08:00:00.123456"},
		{"08:00:00,5", "08:00:00.500000"},
		{"08", "08:00:00"},
		{"08:00:00Z", "08:00:00"},
		{"T0800", "08:00:00"},
		{"8:00", "08:00:00"},
		{"08:0", "08:00:00"},
		{"080000.5", "08:00:00.500000"},
		{"08:00:00+0200", "08:00:00"},
		{"08:00:00-01:30:15.5", "08:00:00"},
		{"٠٨:00", "08:00:00"},
		{"T08", "08:00:00"},
		{"08:00:00.1234567890123", "08:00:00.123456"},
		{"08:00\n", "08:00:00"},
		{"1:2:3", "01:02:03"},
		{"٠٨:٠٠:٠٠.٥", "08:00:00.500000"},
	} {
		got, err := mcpTimeValue(tc.text)
		if err != nil || got != tc.want {
			t.Errorf("time %q: %q %v, want %q", tc.text, got, err, tc.want)
		}
	}
	if got, err := mcpTimeValue(moment); err != nil || got != "08:00:00" {
		t.Errorf("coerced datetime: %q %v", got, err)
	}
	for _, tc := range []struct {
		text string
		want string
	}{
		{"08:00:00 ", mcpMeta.Messages["TimeField.invalid"]},
		{"08:00\n\n", mcpMeta.Messages["TimeField.invalid"]},
		{"08:00:00.", mcpMeta.Messages["TimeField.invalid"]},
		{"24:00", mcpMeta.Messages["TimeField.invalid_time"]},
		{"08:60", mcpMeta.Messages["TimeField.invalid_time"]},
	} {
		_, err := mcpTimeValue(tc.text)
		want := mcpValidationError(tc.want, tc.text).Error()
		if err == nil || err.Error() != want {
			t.Errorf("time %q: %v, want %q", tc.text, err, want)
		}
	}
	for _, value := range []any{json.Number("5"), mcpCoercedDate{time.Date(2025, 9, 1, 0, 0, 0, 0, time.UTC)},
		true, math.Inf(1), []any{}, domain.RequestObject{}} {
		if _, err := mcpTimeValue(value); err == nil || err.Error() != "fromisoformat: argument must be str" {
			t.Errorf("time %T: %v", value, err)
		}
	}

	plan, err := mcpCountPlanJSON(t, `{"model_name":"Course","filters":{"start_date__time":"2025-09-01T08:00:00"}}`)
	if err != nil || !mcpHasArg(mcpPlannedArgs(plan), "08:00:00") {
		t.Errorf("time plan: %v %#v", err, plan.Statements)
	}
	for _, params := range []string{
		`{"model_name":"Course","filters":{"start_date__time":5}}`,
		`{"model_name":"Course","filters":{"start_date__time":"2025-09-01"}}`,
		`{"model_name":"Course","filters":{"start_date__time__in":["08:00","0800",5]}}`,
	} {
		if _, err := mcpCountPlanJSON(t, params); err == nil || err.Error() != "fromisoformat: argument must be str" {
			t.Errorf("%s: %v", params, err)
		}
	}
}

func TestMCPDateTimeOutsideYears(t *testing.T) {
	for _, tc := range []struct {
		params string
		arg    string
	}{
		{`{"model_name":"Course","filters":{"start_date__gt":"9999-12-31T23:00:00-05:00"}}`,
			"9999-12-31T23:00:00-05:00"},
		{`{"model_name":"Course","filters":{"start_date__lt":"0001-01-01T00:00:00+05:00"}}`,
			"0001-01-01T00:00:00+05:00"},
		{`{"model_name":"Course","filters":{"creation_date__lt":"9999-12-31T23:59:59-01:00"}}`,
			"9999-12-31T23:59:59-01:00"},
	} {
		plan, err := mcpCountPlanJSON(t, tc.params)
		if err != nil {
			t.Errorf("%s: %v", tc.params, err)
			continue
		}
		if !mcpHasArg(mcpPlannedArgs(plan), tc.arg) ||
			!strings.Contains(plan.Statements[0].SQL, "::timestamptz") {
			t.Errorf("%s: args %#v, sql %q", tc.params, mcpPlannedArgs(plan), plan.Statements[0].SQL)
		}
	}
}

func TestMCPIntegerOutsideInt64Lists(t *testing.T) {
	plan, err := mcpCountPlanJSON(t, `{"model_name":"Course","filters":{"status_flag__in":[2,99999999999999999999]}}`)
	if err != nil {
		t.Fatalf("in list: %v", err)
	}
	if !mcpHasArg(mcpPlannedArgs(plan), "99999999999999999999") ||
		!strings.Contains(plan.Statements[0].SQL, "::numeric") {
		t.Errorf("in list: %#v %q", plan.Statements, plan.Statements[0].SQL)
	}
	plan, err = mcpCountPlanJSON(t, `{"model_name":"Course","filters":{"status_flag__range":[1,99999999999999999999]}}`)
	if err != nil {
		t.Fatalf("range list: %v", err)
	}
	if !mcpHasArg(mcpPlannedArgs(plan), "99999999999999999999") ||
		!strings.Contains(plan.Statements[0].SQL, "BETWEEN") ||
		!strings.Contains(plan.Statements[0].SQL, "::numeric") {
		t.Errorf("range list: %#v", plan.Statements)
	}
}

func TestMCPJSONValuesAndHasKey(t *testing.T) {
	for _, tc := range []struct {
		key   string
		value any
		arg   string
	}{
		{"meta__b", math.NaN(), "NaN"},
		{"meta", math.Inf(1), "Infinity"},
	} {
		plan, err := mcpCountPlan(t, "Payment", tc.key, tc.value)
		if err != nil || !mcpHasArg(mcpPlannedArgs(plan), tc.arg) {
			t.Errorf("%s %v: %v %#v", tc.key, tc.value, err, plan.Statements)
		}
	}
	if plan, err := mcpCountPlanJSON(t, `{"model_name":"Payment","filters":{"meta__b":1e400}}`); err != nil ||
		!mcpHasArg(mcpPlannedArgs(plan), "Infinity") {
		t.Errorf("meta overflow: %v %#v", err, plan.Statements)
	}
	for _, tc := range []struct {
		value any
	}{
		{math.Inf(1)},
		{math.NaN()},
	} {
		plan, err := mcpCountPlan(t, "Payment", "meta__has_key", tc.value)
		if err != nil || !strings.Contains(plan.Statements[0].SQL, "? $") ||
			!strings.Contains(plan.Statements[0].SQL, "::double precision") {
			t.Errorf("has_key %v: %v %#v", tc.value, err, plan.Statements)
		}
	}
	plan, err := mcpCountPlanJSON(t, `{"model_name":"Payment","filters":{"meta__has_key":1e400}}`)
	if err != nil || !strings.Contains(plan.Statements[0].SQL, "::double precision") ||
		!mcpHasArg(mcpPlannedArgs(plan), math.Inf(1)) {
		t.Errorf("has_key overflow: %v %#v", err, plan.Statements)
	}
	// A coerced value (the walk resolves names after a JSONField): Django
	// sends it, so Go hands the request off.
	if _, err := mcpJSONValue(mcpCoercedInt{big.NewInt(1)}); !errors.Is(err, refusal.ErrNotPorted) {
		t.Errorf("JSON value coerced: %v", err)
	}
	q := newMCPQuery(mcpModelByLabel("application.Payment"))
	if _, err := q.mcpJSONKeyArgument(mcpCoercedInt{big.NewInt(1)}); !errors.Is(err, refusal.ErrNotPorted) {
		t.Errorf("has_key coerced value: %v", err)
	}
	if _, err := q.mcpJSONKeyArray([]any{mcpCoercedInt{big.NewInt(1)}}); !errors.Is(err, refusal.ErrAppState) {
		t.Errorf("has_key unexpected list item: %v", err)
	}
	arraySQL, err := q.mcpJSONKeyArray([]any{math.Inf(1), json.Number("1.5")})
	if err != nil || !strings.HasSuffix(arraySQL, "::numeric[]") ||
		!mcpHasArg(q.args.values, []string{"inf", "1.5"}) {
		t.Errorf("has_key float array: %q %v %#v", arraySQL, err, q.args.values)
	}
}

func TestMCPUnreachablePreparedStates(t *testing.T) {
	q := newMCPQuery(mcpModelByLabel("application.Course"))
	if _, err := q.yearLookup(mcpExpr{extract: "year", column: `"application_course"."start_date"`, colKind: "datetime"},
		"exact", mcpPrepared{}); !errors.Is(err, refusal.ErrAppState) {
		t.Errorf("year without integer: %v", err)
	}
	if _, err := q.prepare(mcpExpr{kind: "json"}, "x", true); !errors.Is(err, refusal.ErrAppState) {
		t.Errorf("prepare json kind: %v", err)
	}
}
