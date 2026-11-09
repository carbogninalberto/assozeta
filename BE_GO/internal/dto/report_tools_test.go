package dto

import (
	"errors"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func reportParams(t *testing.T, text string) domain.RequestObject {
	t.Helper()
	parsed, err := ParseReferenceJSON([]byte(text))
	if err != nil {
		t.Fatal(err)
	}
	return parsed.(domain.RequestObject)
}

func TestReportToolOutcomes(t *testing.T) {
	settings := func() (ReportSettings, error) { return ReportSettings{MaxResults: 5000, QueryTimeout: 10}, nil }
	for _, tc := range []struct {
		tool, params string
		want         error
	}{
		{"query_data", `{"model_name": "Tags", "sport_association_id": "x"}`, ErrReportFailed},
		{"query_data", `{"fields": ["tag_name"]}`, ErrReportFailed},
		{"query_data", `{"model_name": "User"}`, ErrReportFailed},
		{"query_data", `{"model_name": "Payment"}`, ErrReportNotPorted},
		{"query_data", `{"model_name": "Tags", "filters": {"colour": "x"}}`, ErrReportFailed},
		{"query_data", `{"model_name": "Tags", "filters": {"subscription__name": "x"}}`, ErrReportNotPorted},
		{"query_data", `{"model_name": "Tags", "filters": {"tag_name__regex": "x"}}`, ErrReportNotPorted},
		{"query_data", `{"model_name": "Tags", "filters": {"tag_id": "nope"}}`, ErrReportFailed},
		{"query_data", `{"model_name": "Tags", "order_by": ["?"]}`, ErrReportNotPorted},
		{"query_data", `{"model_name": "Tags", "joins": ["subscription"]}`, ErrReportNotPorted},
		{"query_data", `{"model_name": "Tags", "limit": true}`, ErrReportNotPorted},
		{"query_data", `{"model_name": "Tags", "limit": "5"}`, ErrReportFailed},
		{"query_data", `{"model_name": "Carnet", "filters": {"meta__colore": "blu"}}`, ErrReportNotPorted},
		{"get_field_values", `{"model_name": "Tags", "field_name": "password"}`, ErrReportFailed},
		{"aggregate_data", `{"model_name": "Tags", "aggregations": [{"function": "count", "field": "*"}], "group_by": ["creation_date__year"]}`, ErrReportNotPorted},
		{"export_data", `{"model_name": "Tags"}`, ErrReportNotPorted},
		{"get_schema", `{"model_name": "Tags"}`, ErrReportNotPorted},
		{"get_schema", `{}`, ErrReportNotPorted},
	} {
		_, err := PlanReportTool(tc.tool, reportParams(t, tc.params), "a1000000-0000-4000-8000-000000701001", settings)
		if !errors.Is(err, tc.want) {
			t.Errorf("%s %s: got %v, want %v", tc.tool, tc.params, err, tc.want)
		}
	}
}

func TestReportToolPlans(t *testing.T) {
	settings := func() (ReportSettings, error) { return ReportSettings{MaxResults: 50, QueryTimeout: 10}, nil }
	plan, err := PlanReportTool("query_data", reportParams(t, `{"model_name": "Carnet", "fields": ["title"],
		"exclude": {"title": "Cinque"}, "order_by": ["-fee"], "limit": 80}`), "own", settings)
	if err != nil {
		t.Fatal(err)
	}
	query := plan.Statements[1].SQL
	for _, part := range []string{`NOT (("title" = $2::text AND "title" IS NOT NULL))`, `"fee" AS o0`, `ORDER BY o0 DESC`, `LIMIT 50`} {
		if !strings.Contains(query, part) {
			t.Errorf("query %q lacks %q", query, part)
		}
	}
	plan, err = PlanReportTool("get_schema", reportParams(t, `{"model_name": 5}`), "own", settings)
	if err != nil || plan.Immediate == nil || plan.Immediate.Message != "Model 5 not found" {
		t.Fatalf("schema: %v %+v", err, plan.Immediate)
	}
}

func TestReportUnescape(t *testing.T) {
	for input, want := range map[string]string{"Calcio &amp; Basket": "Calcio & Basket", "a & b": "a & b", "x&": "x&", "&lt;p&gt;": "<p>"} {
		got, err := reportUnescape(input)
		if err != nil || got != want {
			t.Errorf("%q: got %v %v", input, got, err)
		}
	}
	for _, input := range []string{"&#39;", "&notit", "&copy;"} {
		if _, err := reportUnescape(input); !errors.Is(err, ErrReportNotPorted) {
			t.Errorf("%q: want refusal, got %v", input, err)
		}
	}
}
