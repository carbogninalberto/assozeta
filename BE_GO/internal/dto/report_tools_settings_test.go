package dto

import (
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"strings"
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
	settings := reportToolSettings(ReportSettings{MaxResults: big.NewInt(0), QueryTimeout: big.NewInt(10)})
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
	settings := reportToolSettings(ReportSettings{MaxResults: big.NewInt(-5), QueryTimeout: big.NewInt(10)})
	_, err := PlanReportTool("query_data", reportParams(t, `{"model_name":"Tags"}`), portedAssociation, settings)
	if err == nil || err.Error() != "Negative indexing is not supported." {
		t.Fatalf("query_data with max_results -5: got %v, want the negative-index ValueError", err)
	}
}

// TestReportToolQueryTimeout is _set_query_timeout's input: the empty string
// (0) and negative ints are ordinary Python values, and PostgreSQL rejects
// them when the statement run sets the timeout, not at plan time.
func TestReportToolQueryTimeout(t *testing.T) {
	for _, timeout := range []int64{0, -1} {
		for _, tool := range []string{"count_data", "query_data"} {
			params := reportParams(t, `{"model_name":"Tags"}`)
			settings := reportToolSettings(ReportSettings{MaxResults: big.NewInt(5000), QueryTimeout: big.NewInt(timeout)})
			plan, err := PlanReportTool(tool, params, portedAssociation, settings)
			if err != nil || len(plan.Statements) == 0 {
				t.Errorf("%s with query_timeout %d: %+v, %v", tool, timeout, plan, err)
			}
		}
	}
}

// TestReportToolQueryTimeoutAggregate is execute_aggregation's order: the
// "no aggregations" answer comes before _set_query_timeout, the group_by
// answer after it.
func TestReportToolQueryTimeoutAggregate(t *testing.T) {
	none := reportParams(t, `{"model_name":"Tags"}`)
	settings := reportToolSettings(ReportSettings{MaxResults: big.NewInt(5000), QueryTimeout: big.NewInt(0)})
	plan, err := PlanReportTool("aggregate_data", none, portedAssociation, settings)
	if err != nil || plan.Immediate == nil || plan.Immediate.Message != "No aggregations specified" {
		t.Fatalf("aggregate_data without aggregations: %+v, %v", plan, err)
	}
	params := reportParams(t,
		`{"model_name":"Tags","aggregations":[{"function":"count","field":"*"}],"group_by":["nope"]}`)
	valid := reportToolSettings(ReportSettings{MaxResults: big.NewInt(5000), QueryTimeout: big.NewInt(10)})
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
			if _, err := mcpSlice(tc.params, big.NewInt(-5)); err == nil || err.Error() != tc.want {
				t.Fatalf("mcpSlice(%+v, -5) = %v, want %q", tc.params, err, tc.want)
			}
		})
	}
	// min(-3, -5) is -5: a negative limit does not change the bounds.
	number, err := mcpEffectiveLimit(json.Number("-3"), big.NewInt(-5))
	if err != nil || number.integer.String() != "-5" {
		t.Fatalf("mcpEffectiveLimit(-3, -5) = %+v, %v", number, err)
	}
}

// TestReportToolBigEffectiveLimit is Python's exact int/float comparison in
// min(limit, MAX): the int is not rounded to a float first, NaN compares
// False and the infinities compare as themselves.
func TestReportToolBigEffectiveLimit(t *testing.T) {
	// 1.5e19 < 10**20: min keeps the limit, which reaches LIMIT as the exact
	// integer 15000000000000000000.
	huge := new(big.Int).Exp(big.NewInt(10), big.NewInt(20), nil)
	number, err := mcpEffectiveLimit(json.Number("1.5e19"), huge)
	if err != nil || !number.isFloat || number.float != 1.5e19 {
		t.Fatalf("min(1.5e19, 10**20) = %+v, %v", number, err)
	}
	bounds, err := mcpSlice(domain.RequestObject{{Name: "limit", Value: json.Number("1.5e19")}}, huge)
	if err != nil || bounds.limit.String() != "15000000000000000000" {
		t.Fatalf("LIMIT for 1.5e19: %+v, %v", bounds, err)
	}
	// float64(2**60 - 1) rounds up to 2**60, but Python compares exactly:
	// 2**60 - 1 < 2.0**60, so min keeps the maximum.
	rounded := new(big.Int).Sub(new(big.Int).Lsh(big.NewInt(1), 60), big.NewInt(1))
	number, err = mcpEffectiveLimit(json.Number("1.152921504606847e18"), rounded)
	if err != nil || number.isFloat || number.integer.String() != rounded.String() {
		t.Fatalf("min(2.0**60, 2**60-1) = %+v, %v", number, err)
	}
	// NaN is kept (MAX < NaN is False); +Inf yields MAX; -Inf is kept.
	if number, err = mcpEffectiveLimit(math.NaN(), huge); err != nil || !number.isFloat || !math.IsNaN(number.float) {
		t.Fatalf("min(NaN, 10**20) = %+v, %v", number, err)
	}
	if number, err = mcpEffectiveLimit(math.Inf(1), huge); err != nil || number.isFloat || number.integer.Cmp(huge) != 0 {
		t.Fatalf("min(inf, 10**20) = %+v, %v", number, err)
	}
	if number, err = mcpEffectiveLimit(math.Inf(-1), huge); err != nil || !number.isFloat || !math.IsInf(number.float, -1) {
		t.Fatalf("min(-inf, 10**20) = %+v, %v", number, err)
	}
}

// TestReportToolBigMaximumLimit is min(MAX, MAX) for a maximum beyond int64
// when the request carries no limit: the SQL LIMIT is the exact decimal.
func TestReportToolBigMaximumLimit(t *testing.T) {
	huge := new(big.Int).Exp(big.NewInt(10), big.NewInt(20), nil)
	settings := reportToolSettings(ReportSettings{MaxResults: huge, QueryTimeout: big.NewInt(10)})
	plan, err := PlanReportTool("query_data", reportParams(t, `{"model_name":"Tags"}`), portedAssociation, settings)
	if err != nil {
		t.Fatal(err)
	}
	if query := plan.Statements[1].SQL; !strings.Contains(query, " LIMIT 100000000000000000000") {
		t.Fatalf("query %q lacks the exact LIMIT", query)
	}
}

// TestReportSettingsQueryTimeoutText is str(query_timeout * 1000): the empty
// text for the 0 stand-in, the exact decimal product otherwise.
func TestReportSettingsQueryTimeoutText(t *testing.T) {
	for _, tc := range []struct {
		timeout string
		want    string
	}{
		{"0", ""},
		{"10", "10000"},
		{"-5", "-5000"},
		{"99999999999999999999999", "99999999999999999999999000"},
	} {
		timeout, _ := new(big.Int).SetString(tc.timeout, 10)
		settings := ReportSettings{MaxResults: big.NewInt(5000), QueryTimeout: timeout}
		if text := settings.QueryTimeoutText(); text != tc.want {
			t.Errorf("QueryTimeoutText(%s) = %q, want %q", tc.timeout, text, tc.want)
		}
	}
}
