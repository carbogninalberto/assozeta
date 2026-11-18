package dto

import (
	"strings"
	"testing"
)

// TestMCPEmptyWhereDropsParameters covers Django's EmptyResultSet at the
// top-level WHERE: the compiler elides every statement, so no parameter of the
// query (before or after the deciding filter) reaches psycopg2, and the
// parameters of a dropped node are not adapted either.
func TestMCPEmptyWhereDropsParameters(t *testing.T) {
	const association = "a1000000-0000-4000-8000-000000701001"
	const dateAdaptError = "Object of type date is not JSON serializable"
	plan := func(t *testing.T, tool, params string) ReportPlan {
		t.Helper()
		planned, err := PlanReportTool(tool, reportParams(t, params), association, mcpValueSettings)
		if err != nil {
			t.Fatalf("%s %s: %v", tool, params, err)
		}
		return planned
	}
	// mcpNoArgs checks a plan whose statements run without parameters.
	mcpNoArgs := func(t *testing.T, tool, params string, planned ReportPlan) {
		t.Helper()
		if len(planned.Statements) == 0 {
			t.Fatalf("%s %s: no statements", tool, params)
		}
		for _, statement := range planned.Statements {
			if len(statement.Args) != 0 {
				t.Errorf("%s %s: statement %q carries args %#v", tool, params, statement.SQL, statement.Args)
			}
		}
	}
	// The date parameter of the first child is never adapted: the empty __in
	// list makes the statement elided, so the adaptation error disappears.
	params := `{"model_name":"Payment","filters":{"meta__payment_date":"2025-03-04","payment_id__in":[]}}`
	counted := plan(t, "count_data", params)
	mcpNoArgs(t, "count_data", params, counted)
	if len(counted.Statements) != 1 || counted.Statements[0].SQL != `SELECT '0'::text` {
		t.Errorf("count_data: statements %+v, want one SELECT '0'::text", counted.Statements)
	}
	params = `{"model_name":"Payment","fields":["amount"],"filters":{"meta__payment_date":"2025-03-04","payment_id__in":[]}}`
	queried := plan(t, "query_data", params)
	mcpNoArgs(t, "query_data", params, queried)
	if len(queried.Statements) != 2 {
		t.Fatalf("query_data: %d statements, want 2", len(queried.Statements))
	}
	if listing := queried.Statements[1].SQL; !strings.Contains(listing, "WHERE FALSE") {
		t.Errorf("query_data: listing %q lacks WHERE FALSE", listing)
	}
	// A later exclude() call is built but never compiled: its parameters and
	// errors are dropped.
	for _, params := range []string{
		`{"model_name":"Payment","filters":{"payment_id__in":[]},"exclude":{"meta__amount":"inf"}}`,
		`{"model_name":"Payment","filters":{"payment_id__in":[]},"exclude":{"meta__amount__has_key":"12"}}`,
	} {
		counted := plan(t, "count_data", params)
		mcpNoArgs(t, "count_data", params, counted)
		if len(counted.Statements) != 1 || counted.Statements[0].SQL != `SELECT '0'::text` {
			t.Errorf("%s: statements %+v, want one SELECT '0'::text", params, counted.Statements)
		}
	}
	// The empty AND branch of the OR is dropped with its date parameter, so
	// the statement is a real COUNT and nothing is adapted.
	params = `{"model_name":"Payment","or_filters":[{"meta__payment_date":"2025-03-04","payment_id__in":[]},{"amount__gt":"0"}]}`
	counted = plan(t, "count_data", params)
	if len(counted.Statements) != 1 || !strings.Contains(counted.Statements[0].SQL, "COUNT(*)") {
		t.Fatalf("or_filters: statements %+v, want a COUNT(*)", counted.Statements)
	}
	for _, argument := range counted.Statements[0].Args {
		switch argument.(type) {
		case mcpCoercedDate, mcpCoercedDateTime:
			t.Errorf("or_filters: args %#v carry a date", counted.Statements[0].Args)
		}
		if argument == "null" {
			t.Errorf("or_filters: args %#v carry the dropped branch's parameter", counted.Statements[0].Args)
		}
	}
	// Without an empty child the date parameter is adapted and the tool
	// reports the error.
	_, err := PlanReportTool("count_data", reportParams(t,
		`{"model_name":"Payment","filters":{"meta__payment_date":"2025-03-04"}}`), association, mcpValueSettings)
	if message := mcpToolError(t, err); message != dateAdaptError {
		t.Errorf("date filter: got %q, want %q", message, dateAdaptError)
	}
	// A flat aggregate answers each aggregate's empty value: Count 0 and the
	// others None.
	params = `{"model_name":"Payment","filters":{"payment_id__in":[]},"aggregations":[{"function":"count"},` +
		`{"function":"sum","field":"amount"}]}`
	aggregated := plan(t, "aggregate_data", params)
	mcpNoArgs(t, "aggregate_data", params, aggregated)
	if len(aggregated.Statements) != 1 || aggregated.Statements[0].SQL != `SELECT '0'::text, NULL::text` {
		t.Errorf("aggregate_data: statements %+v, want SELECT '0'::text, NULL::text", aggregated.Statements)
	}
}
