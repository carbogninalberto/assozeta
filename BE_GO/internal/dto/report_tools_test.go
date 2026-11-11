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
		{"query_data", `{"model_name": "Payment", "filters": {"type__regex": "x"}}`, nil},
		{"query_data", `{"model_name": "Tags", "filters": {"colour": "x"}}`, ErrReportFailed},
		{"query_data", `{"model_name": "Tags", "exclude": {"subscription__status_flag": 1}}`, ErrReportNotPorted},
		{"query_data", `{"model_name": "Tags", "filters": {"tag_name__regex": "x"}}`, nil},
		{"query_data", `{"model_name": "Tags", "filters": {"tag_id": "nope"}}`, ErrReportFailed},
		{"query_data", `{"model_name": "Tags", "order_by": ["?"]}`, ErrReportNotPorted},
		{"query_data", `{"model_name": "Tags", "filters": {"creation_date__minute": 1}}`, nil},
		{"query_data", `{"model_name": "Tags", "limit": true}`, ErrReportNotPorted},
		{"query_data", `{"model_name": "Tags", "limit": "5"}`, ErrReportFailed},
		{"query_data", `{"model_name": "Carnet", "filters": {"meta__colore__icontains": "blu"}}`, ErrReportNotPorted},
		{"get_field_values", `{"model_name": "Tags", "field_name": "password"}`, ErrReportFailed},
		{"aggregate_data", `{"model_name": "Tags", "aggregations": [{"function": "sum", "field": "tag_name"}]}`, ErrReportFailed},
		{"export_data", `{"model_name": "Tags"}`, ErrReportNotPorted},
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
		"exclude": {"title": "Cinque"}, "order_by": ["-fee"], "limit": 80}`), "a1000000-0000-4000-8000-000000701001", settings)
	if err != nil {
		t.Fatal(err)
	}
	query := plan.Statements[1].SQL
	for _, part := range []string{`NOT ("application_carnet"."title" = $2 AND "application_carnet"."title" IS NOT NULL)`,
		`"application_carnet"."fee" AS "__o0"`, `ORDER BY "application_carnet"."fee" DESC`, `LIMIT 50`} {
		if !strings.Contains(query, part) {
			t.Errorf("query %q lacks %q", query, part)
		}
	}
	if _, err = PlanReportTool("count_data", reportParams(t, `{"model_name": "Tags"}`), "own", settings); err == nil ||
		err.Error() != `['"own" non è uno UUID valido.']` {
		t.Errorf("association: %v", err)
	}
	plan, err = PlanReportTool("get_schema", reportParams(t, `{"model_name": 5}`), "own", settings)
	if err != nil || plan.Immediate == nil || plan.Immediate.Message != "Model 5 not found" {
		t.Fatalf("schema: %v %+v", err, plan.Immediate)
	}
}

func TestReportToolLookupFamilies(t *testing.T) {
	settings := func() (ReportSettings, error) { return ReportSettings{MaxResults: 5000, QueryTimeout: 10}, nil }
	plan := func(t *testing.T, params string) ReportPlan {
		t.Helper()
		planned, err := PlanReportTool("query_data", reportParams(t, params), "a1000000-0000-4000-8000-000000701001", settings)
		if err != nil {
			t.Fatalf("%s: %v", params, err)
		}
		return planned
	}
	for _, tc := range []struct {
		params string
		sql    []string
		args   []any
	}{
		{`{"model_name":"Course","filters":{"title__regex":"^Nuoto"}}`,
			[]string{`"application_course"."title"::text ~ $`}, []any{"^Nuoto"}},
		{`{"model_name":"Course","filters":{"title__iregex":5}}`,
			[]string{`"application_course"."title"::text ~* $`}, []any{int32(5)}},
		{`{"model_name":"Course","filters":{"fee__contains":"5"}}`,
			[]string{`"application_course"."fee"::text LIKE $`}, []any{"%5.0%"}},
		{`{"model_name":"Course","filters":{"fee__icontains":45}}`,
			[]string{`UPPER("application_course"."fee"::text) LIKE UPPER($`}, []any{"%45%"}},
		{`{"model_name":"Course","filters":{"status_flag__iexact":"2"}}`,
			[]string{`UPPER("application_course"."status_flag"::text) = UPPER($`}, []any{int32(2)}},
		{`{"model_name":"Course","filters":{"course_id__gt":"00000000-0000-0000-0000-000000000000"}}`,
			[]string{`"application_course"."course_id" > $`}, nil},
		{`{"model_name":"Course","filters":{"pinned__lte":true}}`,
			[]string{`"application_course"."pinned" <= $`}, []any{true}},
		{`{"model_name":"Course","filters":{"sport_association__gt":"00000000-0000-0000-0000-000000000000"}}`,
			[]string{`"application_course"."sport_association_id" > $`}, nil},
		{`{"model_name":"Course","filters":{"start_date__minute":0}}`,
			[]string{`EXTRACT(MINUTE FROM "application_course"."start_date" AT TIME ZONE 'UTC') = $`}, nil},
		{`{"model_name":"Course","filters":{"start_date__second":0}}`,
			[]string{`EXTRACT(SECOND FROM DATE_TRUNC('second', "application_course"."start_date" AT TIME ZONE 'UTC')) = $`}, nil},
		{`{"model_name":"Course","filters":{"start_date__time":"08:00:00"}}`,
			[]string{`("application_course"."start_date" AT TIME ZONE 'UTC')::time = $`}, nil},
		{`{"model_name":"Course","filters":{"start_date__iso_year":2025}}`,
			[]string{`"application_course"."start_date" BETWEEN $`, ` AND $`}, []any{"2024-12-30T00:00:00+00:00", "2025-12-28T23:59:59.999999+00:00"}},
		{`{"model_name":"Course","filters":{"start_date__iso_week_day":1}}`,
			[]string{`EXTRACT(ISODOW FROM "application_course"."start_date" AT TIME ZONE 'UTC') = $`}, nil},
		{`{"model_name":"Course","filters":{"start_date__year":null}}`,
			[]string{`EXTRACT(YEAR FROM "application_course"."start_date" AT TIME ZONE 'UTC') IS NULL`}, nil},
		{`{"model_name":"Course","filters":{"start_date__date__year":2025}}`,
			[]string{`("application_course"."start_date" AT TIME ZONE 'UTC')::date BETWEEN $`}, []any{"2025-01-01", "2025-12-31"}},
		{`{"model_name":"Course","filters":{"start_date__time__hour":8}}`,
			[]string{`EXTRACT(HOUR FROM ("application_course"."start_date" AT TIME ZONE 'UTC')::time) = $`}, nil},
		{`{"model_name":"Course","filters":{"start_date__month":9.5}}`,
			[]string{`EXTRACT(MONTH FROM "application_course"."start_date" AT TIME ZONE 'UTC') = $`}, []any{int64(9)}},
	} {
		planned := plan(t, tc.params)
		query := planned.Statements[1].SQL
		for _, part := range tc.sql {
			if !strings.Contains(query, part) {
				t.Errorf("%s: query %q lacks %q", tc.params, query, part)
			}
		}
		for _, want := range tc.args {
			found := false
			for _, argument := range planned.Statements[1].Args {
				if argument == want {
					found = true
				}
			}
			if !found {
				t.Errorf("%s: args %#v lack %#v", tc.params, planned.Statements[1].Args, want)
			}
		}
	}
	for params, want := range map[string]string{
		`{"model_name":"Course","filters":{"title__nope":"x"}}`:                          "Unsupported lookup 'nope' for CharField or join on the field not permitted.",
		`{"model_name":"Course","filters":{"title__exact__nope":"x"}}`:                   "Unsupported lookup 'exact__nope' for CharField or join on the field not permitted, perhaps you meant exact or iexact?",
		`{"model_name":"Course","filters":{"title__lower":"x"}}`:                         "Unsupported lookup 'lower' for CharField or join on the field not permitted.",
		`{"model_name":"Course","filters":{"title__length":5}}`:                          "Unsupported lookup 'length' for CharField or join on the field not permitted.",
		`{"model_name":"Course","filters":{"title__search":"x"}}`:                        "Unsupported lookup 'search' for CharField or join on the field not permitted.",
		`{"model_name":"Course","filters":{"sport_association__regex":"x"}}`:             "Unsupported lookup 'regex' for ForeignKey or join on the field not permitted.",
		`{"model_name":"Course","filters":{"sport_association__contains":"x"}}`:          "Unsupported lookup 'contains' for ForeignKey or join on the field not permitted.",
		`{"model_name":"Course","filters":{"title__":"x"}}`:                              "Unsupported lookup '' for CharField or join on the field not permitted.",
		`{"model_name":"Course","filters":{"title____exact":"x"}}`:                       "Unsupported lookup '__exact' for CharField or join on the field not permitted.",
		`{"model_name":"Associate","filters":{"born_date__hour":1}}`:                     "Unsupported lookup 'hour' for DateField or join on the field not permitted.",
		`{"model_name":"Associate","filters":{"born_date__date":"1990-05-01"}}`:          "Unsupported lookup 'date' for DateField or join on the field not permitted.",
		`{"model_name":"Course","filters":{"start_date__year":0}}`:                       "year 0 is out of range",
		`{"model_name":"Course","filters":{"start_date__year":10000}}`:                   "year 10000 is out of range",
		`{"model_name":"Course","filters":{"start_date__year__lt":20000}}`:               "year 20000 is out of range",
		`{"model_name":"Course","filters":{"start_date__month":99999999999999999999}}`:   "",
		`{"model_name":"Associate","filters":{"born_date":"1990-05-01T10:00:00+02:00"}}`: `['Il valore "1990-05-01T10:00:00+02:00" ha un formato di data non valido. Deve essere nel formato AAAA-MM-GG.']`,
		`{"model_name":"Associate","filters":{"born_date":5}}`:                           "fromisoformat: argument must be str",
	} {
		_, err := PlanReportTool("query_data", reportParams(t, params), "a1000000-0000-4000-8000-000000701001", settings)
		if err == nil || err.Error() != want {
			t.Errorf("%s: got %v, want %q", params, err, want)
		}
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
