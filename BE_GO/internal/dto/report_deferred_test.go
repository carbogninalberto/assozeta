package dto

import (
	"errors"
	"math/big"
	"strings"
	"testing"
)

// TestReportDeferredAfterCount is execute_query's and get_field_values'
// order: Django counts first, so an exception of the later planning steps
// (ordering, fields, slice, timeout) is deferred behind the count statement,
// whose own database error wins.
func TestReportDeferredAfterCount(t *testing.T) {
	settings := reportToolSettings(ReportSettings{MaxResults: big.NewInt(5000), QueryTimeout: big.NewInt(10)})
	deferredOf := func(t *testing.T, err error) *ReportDeferred {
		t.Helper()
		var deferred *ReportDeferred
		if !errors.As(err, &deferred) {
			t.Fatalf("err %v is not a *ReportDeferred", err)
		}
		if deferred.Err == nil {
			t.Fatal("deferred error without a cause")
		}
		return deferred
	}

	t.Run("execute-query-negative-slice", func(t *testing.T) {
		plan, err := PlanReportTool("query_data", reportParams(t,
			`{"model_name":"Payment","fields":["amount"],"filters":{"meta__has_key":12},"limit":-1}`),
			portedAssociation, settings)
		if err == nil || err.Error() != "Negative indexing is not supported." {
			t.Fatalf("got %v, want the negative-index ValueError", err)
		}
		deferred := deferredOf(t, err)
		if !deferred.Untimed {
			t.Error("execute_query's deferred exception runs before the SET")
		}
		if len(plan.Statements) != 1 || !strings.Contains(plan.Statements[0].SQL, "COUNT(*)") {
			t.Fatalf("statements %+v, want the count alone", plan.Statements)
		}
	})

	t.Run("execute-query-order-by", func(t *testing.T) {
		// _allowed_order_by iterates the value (report_tools_test.go asserts
		// the same text at plan time).
		plan, err := PlanReportTool("query_data", reportParams(t,
			`{"model_name":"Tags","fields":["tag_name"],"order_by":5}`), portedAssociation, settings)
		if err == nil || err.Error() != "'int' object is not iterable" {
			t.Fatalf("got %v, want the TypeError of the ordering", err)
		}
		if !deferredOf(t, err).Untimed {
			t.Error("execute_query's deferred exception runs before the SET")
		}
		if len(plan.Statements) != 1 || !strings.Contains(plan.Statements[0].SQL, "COUNT(*)") {
			t.Fatalf("statements %+v, want the count alone", plan.Statements)
		}
	})

	t.Run("execute-query-timeout", func(t *testing.T) {
		timeoutErr := errors.New("timeout rejected")
		calls := 0
		plan, err := PlanReportTool("query_data", reportParams(t, `{"model_name":"Tags"}`), portedAssociation,
			reportTimeoutSettings(&calls, timeoutErr))
		if !errors.Is(err, timeoutErr) || calls != 1 {
			t.Fatalf("calls %d err %v, want the SET error deferred", calls, err)
		}
		deferred := deferredOf(t, err)
		if !errors.Is(deferred.Err, timeoutErr) || !deferred.Untimed {
			t.Fatalf("deferred %+v, want the SET error before the count", deferred)
		}
		if len(plan.Statements) != 1 || !strings.Contains(plan.Statements[0].SQL, "COUNT(*)") {
			t.Fatalf("statements %+v, want the count alone", plan.Statements)
		}
	})

	t.Run("field-values-negative-slice", func(t *testing.T) {
		plan, err := PlanReportTool("get_field_values", reportParams(t,
			`{"model_name":"Tags","field_name":"tag_name","limit":-1}`), portedAssociation, settings)
		if err == nil || err.Error() != "Negative indexing is not supported." {
			t.Fatalf("got %v, want the negative-index ValueError", err)
		}
		deferred := deferredOf(t, err)
		if deferred.Untimed {
			t.Error("get_field_values counted after the SET, so the count runs under the timeout")
		}
		if len(plan.Statements) != 1 || !strings.Contains(plan.Statements[0].SQL, "COUNT(*)") {
			t.Fatalf("statements %+v, want the count alone", plan.Statements)
		}
	})

	t.Run("count-data-timeout", func(t *testing.T) {
		// count() runs the SET before qs.count(): nothing is deferred.
		timeoutErr := errors.New("timeout rejected")
		calls := 0
		_, err := PlanReportTool("count_data", reportParams(t, `{"model_name":"Tags"}`), portedAssociation,
			reportTimeoutSettings(&calls, timeoutErr))
		if !errors.Is(err, timeoutErr) || calls != 1 {
			t.Fatalf("calls %d err %v, want the SET error at plan time", calls, err)
		}
		var deferred *ReportDeferred
		if errors.As(err, &deferred) {
			t.Fatalf("err %v is deferred, want the plan-time error", err)
		}
	})
}
