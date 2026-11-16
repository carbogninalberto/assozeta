package dto

import (
	"encoding/json"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// reportToolSettings is the QueryBuilder.__init__ callback with fixed values.
func reportToolSettings(values ReportSettings) func() (ReportSettings, error) {
	return func() (ReportSettings, error) { return values, nil }
}

// TestReportToolEmptyMaxResults is `min(limit or self.max_results, ...)` with
// max_results the empty string (0): every path raises a TypeError, so the
// tool raises.
func TestReportToolEmptyMaxResults(t *testing.T) {
	settings := reportToolSettings(ReportSettings{MaxResults: 0, QueryTimeout: 10})
	_, err := PlanReportTool("query_data", reportParams(t, `{"model_name":"Tags"}`), portedAssociation, settings)
	if !errors.Is(err, ErrReportFailed) {
		t.Fatalf("query_data with max_results 0: got %v, want ErrReportFailed", err)
	}
	// count() never computes the effective limit, so it still answers.
	plan, err := PlanReportTool("count_data", reportParams(t, `{"model_name":"Tags"}`), portedAssociation, settings)
	if err != nil || len(plan.Statements) != 1 {
		t.Fatalf("count_data with max_results 0: %+v, %v", plan, err)
	}
}

// TestReportToolNegativeMaxResults is a negative max_results, an ordinary
// Python int: min() keeps it and the slice's stop is negative.
func TestReportToolNegativeMaxResults(t *testing.T) {
	settings := reportToolSettings(ReportSettings{MaxResults: -5, QueryTimeout: 10})
	_, err := PlanReportTool("query_data", reportParams(t, `{"model_name":"Tags"}`), portedAssociation, settings)
	if err == nil || err.Error() != "Negative indexing is not supported." {
		t.Fatalf("query_data with max_results -5: got %v, want the negative-index ValueError", err)
	}
}

// TestReportToolQueryTimeout is _set_query_timeout: SET statement_timeout
// rejects the empty string (0) and every value <= 0, so the tool raises at
// that point.
func TestReportToolQueryTimeout(t *testing.T) {
	for _, timeout := range []int64{0, -1} {
		for _, tool := range []string{"count_data", "query_data"} {
			params := reportParams(t, `{"model_name":"Tags"}`)
			settings := reportToolSettings(ReportSettings{MaxResults: 5000, QueryTimeout: timeout})
			_, err := PlanReportTool(tool, params, portedAssociation, settings)
			if !errors.Is(err, ErrReportFailed) {
				t.Errorf("%s with query_timeout %d: got %v, want ErrReportFailed", tool, timeout, err)
			}
		}
	}
}

// TestReportToolQueryTimeoutAggregate is execute_aggregation's order: the two
// early answers come before _set_query_timeout, the group_by answer after it.
func TestReportToolQueryTimeoutAggregate(t *testing.T) {
	none := reportParams(t, `{"model_name":"Tags"}`)
	settings := reportToolSettings(ReportSettings{MaxResults: 5000, QueryTimeout: 0})
	plan, err := PlanReportTool("aggregate_data", none, portedAssociation, settings)
	if err != nil || plan.Immediate == nil || plan.Immediate.Message != "No aggregations specified" {
		t.Fatalf("aggregate_data without aggregations: %+v, %v", plan, err)
	}
	params := reportParams(t,
		`{"model_name":"Tags","aggregations":[{"function":"count","field":"*"}],"group_by":["nope"]}`)
	if _, err := PlanReportTool("aggregate_data", params, portedAssociation, settings); !errors.Is(err, ErrReportFailed) {
		t.Fatalf("invalid group_by with query_timeout 0: got %v, want ErrReportFailed", err)
	}
	valid := reportToolSettings(ReportSettings{MaxResults: 5000, QueryTimeout: 10})
	plan, err = PlanReportTool("aggregate_data", params, portedAssociation, valid)
	if err != nil || plan.Immediate == nil || plan.Immediate.Message != "No valid group_by fields" {
		t.Fatalf("invalid group_by with a valid timeout: %+v, %v", plan, err)
	}
}

// TestReportToolNegativeMaxResultsSlice is min() and the slice bounds with a
// negative maximum: min(limit, MAX) keeps MAX unless MAX < limit.
func TestReportToolNegativeMaxResultsSlice(t *testing.T) {
	for _, tc := range []struct {
		name   string
		params domain.RequestObject
		want   string
	}{
		{"no-limit", domain.RequestObject{}, "Negative indexing is not supported."},
		{"larger-limit", domain.RequestObject{{Name: "limit", Value: json.Number("10")}}, "Negative indexing is not supported."},
	} {
		t.Run(tc.name, func(t *testing.T) {
			if _, err := mcpSlice(tc.params, -5); err == nil || err.Error() != tc.want {
				t.Fatalf("mcpSlice(%+v, -5) = %v, want %q", tc.params, err, tc.want)
			}
		})
	}
	// min(-3, -5) is -5: a negative limit does not change the bounds.
	number, err := mcpEffectiveLimit(json.Number("-3"), -5)
	if err != nil || number.integer.String() != "-5" {
		t.Fatalf("mcpEffectiveLimit(-3, -5) = %+v, %v", number, err)
	}
}
