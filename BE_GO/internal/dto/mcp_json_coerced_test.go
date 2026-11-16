package dto

import (
	"errors"
	"math"
	"math/big"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func mcpQueryPlan(t *testing.T, params string) (ReportPlan, error) {
	t.Helper()
	return PlanReportTool("query_data", reportParams(t, params), "a1000000-0000-4000-8000-000000701001", mcpValueSettings)
}

func mcpToolError(t *testing.T, err error) string {
	t.Helper()
	if err == nil {
		t.Fatal("want an error, got nil")
	}
	var toolErr *MCPToolException
	if !errors.As(err, &toolErr) {
		t.Fatalf("want an MCPToolException, got %T: %v", err, err)
	}
	return toolErr.Message
}

func TestMCPJSONCoercedValues(t *testing.T) {
	meta := `"application_payment"."meta"`
	for _, tc := range []struct {
		params string
		sql    []string
		arg    string
	}{
		// The coercion walk resolves the names after the JSONField on the
		// same model: amount is a DecimalField, so "12" is the float 12.0.
		{`{"model_name":"Payment","filters":{"meta__amount":"12"}}`,
			[]string{`(` + meta + ` -> $`, `) = $`, `::jsonb`}, "12.0"},
		{`{"model_name":"Payment","filters":{"meta__amount__gt":"5"}}`,
			[]string{`(` + meta + ` -> $`, `) > $`, `::jsonb`}, "5.0"},
		{`{"model_name":"Payment","filters":{"meta__amount__contains":"12"}}`,
			[]string{`(` + meta + ` -> $`, `) @> $`, `::jsonb`}, "12.0"},
		// json.dumps of an int of any size is its decimal digits.
		{`{"model_name":"Payment","filters":{"meta__subject":"999999999999999999999999999999"}}`,
			[]string{`(` + meta + ` -> $`, `) = $`, `::jsonb`}, "999999999999999999999999999999"},
		// json.dumps of a float: repr, Infinity and NaN.
		{`{"model_name":"Payment","filters":{"meta__amount":"inf"}}`,
			[]string{`(` + meta + ` -> $`, `) = $`, `::jsonb`}, "Infinity"},
		{`{"model_name":"Payment","filters":{"meta__amount":"-inf"}}`,
			[]string{`(` + meta + ` -> $`, `) = $`, `::jsonb`}, "-Infinity"},
		{`{"model_name":"Payment","filters":{"meta__amount":"nan"}}`,
			[]string{`(` + meta + ` -> $`, `) = $`, `::jsonb`}, "NaN"},
	} {
		plan, err := mcpCountPlanJSON(t, tc.params)
		if err != nil {
			t.Errorf("%s: %v", tc.params, err)
			continue
		}
		statement := plan.Statements[0]
		reportBindings(t, tc.params, statement.SQL, statement.Args)
		if strings.Contains(statement.SQL, "->>") {
			t.Errorf("%s: query %q reads the key as text", tc.params, statement.SQL)
		}
		for _, part := range tc.sql {
			if !strings.Contains(statement.SQL, part) {
				t.Errorf("%s: query %q lacks %q", tc.params, statement.SQL, part)
			}
		}
		if !mcpHasArg(statement.Args, tc.arg) {
			t.Errorf("%s: args %#v lack %q", tc.params, statement.Args, tc.arg)
		}
	}
}

func TestMCPJSONCoercedHasKey(t *testing.T) {
	meta := `"application_payment"."meta"`
	for _, tc := range []struct {
		params string
		cast   string
		arg    any
	}{
		// psycopg2 writes the coerced Python value as a literal of its type.
		{`{"model_name":"Payment","filters":{"meta__subject__has_key":"12"}}`, `::int`, int64(12)},
		{`{"model_name":"Payment","filters":{"meta__subject__has_key":"3000000000"}}`, `::bigint`, int64(3000000000)},
		{`{"model_name":"Payment","filters":{"meta__subject__has_key":"99999999999999999999"}}`, `::numeric`,
			"99999999999999999999"},
		{`{"model_name":"Payment","filters":{"meta__amount__has_key":"12"}}`, `::numeric`, 12.0},
		{`{"model_name":"Payment","filters":{"meta__amount__has_key":"inf"}}`, `::double precision`, math.Inf(1)},
		{`{"model_name":"Payment","filters":{"meta__amount__has_key":"nan"}}`, `::double precision`, math.NaN()},
		{`{"model_name":"Payment","filters":{"meta__payment_date__has_key":"2025-03-04"}}`, `::date`, "2025-03-04"},
		{`{"model_name":"Payment","filters":{"meta__creation_date__has_key":"2025-03-04T10:00:00"}}`, `::timestamp`,
			"2025-03-04 10:00:00"},
	} {
		plan, err := mcpCountPlanJSON(t, tc.params)
		if err != nil {
			t.Errorf("%s: %v", tc.params, err)
			continue
		}
		statement := plan.Statements[0]
		reportBindings(t, tc.params, statement.SQL, statement.Args)
		if !strings.Contains(statement.SQL, `(`+meta+` -> $`) || !strings.Contains(statement.SQL, `) ? $`) ||
			!strings.Contains(statement.SQL, tc.cast) {
			t.Errorf("%s: query %q lacks the has_key argument cast %q", tc.params, statement.SQL, tc.cast)
		}
		if !mcpHasArg(statement.Args, tc.arg) {
			t.Errorf("%s: args %#v lack %#v", tc.params, statement.Args, tc.arg)
		}
	}
}

func TestMCPJSONCoercedAdaptation(t *testing.T) {
	// A date or datetime value cannot be dumped as JSON: psycopg2 raises when
	// the statement executes, so the plan reports it and leaves the SQL valid.
	for _, tc := range []struct {
		params string
		text   string
	}{
		{`{"model_name":"Payment","filters":{"meta__payment_date":"2025-03-04"}}`,
			"Object of type date is not JSON serializable"},
		{`{"model_name":"Payment","filters":{"meta__creation_date":"2025-03-04T10:00:00"}}`,
			"Object of type datetime is not JSON serializable"},
	} {
		for _, tool := range []string{"count_data", "query_data"} {
			params := reportParams(t, tc.params)
			if tool == "query_data" {
				params = append(params, domain.RequestField{Name: "fields", Value: []any{"amount"}})
			}
			_, err := PlanReportTool(tool, params, "a1000000-0000-4000-8000-000000701001", mcpValueSettings)
			if message := mcpToolError(t, err); message != tc.text {
				t.Errorf("%s %s: got %q, want %q", tool, tc.params, message, tc.text)
			}
		}
	}
	// The aggregation that answers without a statement does not raise it.
	plan, err := PlanReportTool("aggregate_data", reportParams(t,
		`{"model_name":"Payment","filters":{"meta__payment_date":"2025-03-04"},"aggregations":[]}`),
		"a1000000-0000-4000-8000-000000701001", mcpValueSettings)
	if err != nil || plan.Immediate == nil || plan.Immediate.Message != "No aggregations specified" {
		t.Errorf("aggregation without statements: %v %+v", err, plan.Immediate)
	}
	// The lookup itself records the adaptation error and leaves a valid
	// placeholder, so only the statement execution reports it.
	q := newMCPQuery(mcpModelByLabel("application.Payment"))
	lhs := mcpExpr{kind: "json", sql: `"application_payment"."meta" -> 'k'`}
	lookup, err := q.jsonLookup(lhs, "exact", mcpCoercedDateTime{})
	if err != nil || q.adaptErr == nil || lookup.sql != lhs.sql+" = $1::jsonb" ||
		len(q.args.values) != 1 || q.args.values[0] != "null" {
		t.Errorf("lookup adaptation: %v %q %#v", err, lookup.sql, q.args.values)
	}
	// compileErr wins: the compiler runs before psycopg2 adapts the parameters.
	_, err = mcpCountPlanJSON(t, `{"model_name":"Payment","filters":{"meta__payment_date":"2025-03-04","meta__b__isnull":"yes"}}`)
	if message := mcpToolError(t, err); message != "The QuerySet value for an isnull lookup must be True or False." {
		t.Errorf("compile error: got %q", message)
	}
	// split_exclude builds its subquery on a separate query: its adaptation
	// error belongs to the outer statement.
	_, err = mcpCountPlanJSON(t,
		`{"model_name":"Course","exclude":{"coursesubscription__multiple_quote__creation_date":"2025-03-04"}}`)
	if message := mcpToolError(t, err); message != "Object of type date is not JSON serializable" {
		t.Errorf("split exclude: got %q", message)
	}
}

func TestMCPAdaptErrFirstAndClone(t *testing.T) {
	q := newMCPQuery(mcpModelByLabel("application.Payment"))
	first, second := mcpRaise("first"), mcpRaise("second")
	q.recordAdaptErr(first)
	q.recordAdaptErr(second)
	if q.adaptErr != first {
		t.Errorf("recordAdaptErr kept %v, want the first error", q.adaptErr)
	}
	if q.clone().adaptErr != first {
		t.Errorf("clone lost the adaptation error: %v", q.clone().adaptErr)
	}
}

func TestMCPJSONKeyIExactNone(t *testing.T) {
	meta := `"application_payment"."meta"`
	plan, err := mcpQueryPlan(t, `{"model_name":"Payment","fields":["amount"],"filters":{"meta__k__iexact":null}}`)
	if err != nil {
		t.Fatal(err)
	}
	query := plan.Statements[1].SQL
	if !strings.Contains(query, `(`+meta+` -> $`) || !strings.Contains(query, `) IS NULL`) {
		t.Errorf("key iexact None: query %q lacks the key expression IS NULL", query)
	}
	if strings.Contains(query, "->>") {
		t.Errorf("key iexact None: query %q reads the key as text", query)
	}
	for _, statement := range plan.Statements {
		reportBindings(t, "key iexact None", statement.SQL, statement.Args)
	}
	plan, err = mcpQueryPlan(t, `{"model_name":"Payment","fields":["amount"],"filters":{"meta__iexact":null}}`)
	if err != nil {
		t.Fatal(err)
	}
	if query = plan.Statements[1].SQL; !strings.Contains(query, meta+` IS NULL`) {
		t.Errorf("column iexact None: query %q lacks the column IS NULL", query)
	}
	// Other text lookups still reject None.
	_, err = mcpQueryPlan(t, `{"model_name":"Payment","filters":{"meta__note__icontains":null}}`)
	if message := mcpToolError(t, err); message != "Cannot use None as a query value" {
		t.Errorf("icontains None: got %q", message)
	}
}

func TestMCPJSONValueCoercedText(t *testing.T) {
	wide, _ := new(big.Int).SetString("999999999999999999999999999999", 10)
	for _, tc := range []struct {
		value any
		text  string
	}{
		{mcpCoercedInt{big.NewInt(-7)}, "-7"},
		{mcpCoercedInt{wide}, "999999999999999999999999999999"},
		{mcpCoercedFloat{12.0}, "12.0"},
		{mcpCoercedFloat{math.Inf(-1)}, "-Infinity"},
		{mcpCoercedFloat{math.NaN()}, "NaN"},
	} {
		text, adaptErr, err := mcpJSONValue(tc.value)
		if err != nil || adaptErr != nil || text != tc.text {
			t.Errorf("%#v: got %q (adapt %v, err %v), want %q", tc.value, text, adaptErr, err, tc.text)
		}
	}
	for _, value := range []any{mcpCoercedDate{}, mcpCoercedDateTime{}} {
		text, adaptErr, err := mcpJSONValue(value)
		if err != nil || adaptErr == nil || text != "" {
			t.Errorf("%T: got %q (adapt %v, err %v)", value, text, adaptErr, err)
		}
	}
	if _, _, err := mcpJSONValue(7); !errors.Is(err, errMCPUnexpectedValue) {
		t.Errorf("unexpected value: %v", err)
	}
}
