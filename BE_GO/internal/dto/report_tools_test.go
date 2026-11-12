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
		{"query_data", `{"model_name": "Tags", "limit": true}`, nil},
		{"query_data", `{"model_name": "Tags", "limit": "5"}`, ErrReportFailed},
		{"query_data", `{"model_name": "Carnet", "filters": {"meta__colore__icontains": "blu"}}`, ErrReportNotPorted},
		{"get_field_values", `{"model_name": "Tags", "field_name": "password"}`, ErrReportFailed},
		{"aggregate_data", `{"model_name": "Tags", "aggregations": [{"function": "sum", "field": "tag_name"}]}`, nil},
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

func TestReportToolIntegerOverflowOrigins(t *testing.T) {
	settings := func() (ReportSettings, error) { return ReportSettings{MaxResults: 5000, QueryTimeout: 10}, nil }
	plan := func(t *testing.T, params string) error {
		t.Helper()
		_, err := PlanMCPTool("query_data", reportParams(t, params), "a1000000-0000-4000-8000-000000701001", settings)
		return err
	}
	// Inside or_filters or exclude the folded TRUE/FALSE does not mean the
	// same as in Django, so the request is handed to Django.
	for _, params := range []string{
		`{"model_name":"Course","fields":["title"],"or_filters":[{"start_date__month":99999999999999999999},{"title":"Nuoto Adulti"}]}`,
		`{"model_name":"Course","fields":["title"],"exclude":{"start_date__month":99999999999999999999}}`,
	} {
		if err := plan(t, params); !errors.Is(err, ErrReportNotPorted) {
			t.Errorf("%s: got %v, want ErrReportNotPorted", params, err)
		}
	}
	// A plain AND condition of filters folds to an always-false WHERE clause and
	// runs normally: Django answers with no rows instead of raising.
	planned, err := PlanMCPTool("query_data", reportParams(t,
		`{"model_name":"Course","fields":["title"],"filters":{"start_date__month":99999999999999999999}}`),
		"a1000000-0000-4000-8000-000000701001", settings)
	if err != nil {
		t.Fatalf("plain filters: %v", err)
	}
	if len(planned.Statements) == 0 || !strings.Contains(planned.Statements[len(planned.Statements)-1].SQL, "FALSE") {
		t.Errorf("plain filters: got %+v, want a plan whose statement contains FALSE", planned.Statements)
	}
}

func TestReportToolEmptyInList(t *testing.T) {
	settings := func() (ReportSettings, error) { return ReportSettings{MaxResults: 5000, QueryTimeout: 10}, nil }
	// An empty __in list folds to FALSE: the query runs normally and matches no
	// row, exactly like Django (no exception with an empty message).
	for _, tool := range []string{"query_data", "count_data", "get_field_values"} {
		params := map[string]string{
			"query_data":       `{"model_name":"Tags","fields":["tag_name"],"filters":{"tag_id__in":[]}}`,
			"count_data":       `{"model_name":"Tags","filters":{"tag_id__in":[]}}`,
			"get_field_values": `{"model_name":"Tags","field_name":"tag_name","filters":{"tag_id__in":[]}}`,
		}[tool]
		planned, err := PlanReportTool(tool, reportParams(t, params), "a1000000-0000-4000-8000-000000701001", settings)
		if err != nil {
			t.Fatalf("%s: %v", tool, err)
		}
		if len(planned.Statements) == 0 {
			t.Fatalf("%s: no statements", tool)
		}
		for _, statement := range planned.Statements {
			if !strings.Contains(statement.SQL, "FALSE") {
				t.Errorf("%s: statement SQL %q lacks FALSE", tool, statement.SQL)
			}
		}
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
		`{"model_name":"Associate","filters":{"born_date":"1990-05-01T10:00:00+02:00"}}`: `['Il valore "1990-05-01T10:00:00+02:00" ha un formato di data non valido. Deve essere nel formato AAAA-MM-GG.']`,
		`{"model_name":"Associate","filters":{"born_date":5}}`:                           "fromisoformat: argument must be str",
	} {
		_, err := PlanReportTool("query_data", reportParams(t, params), "a1000000-0000-4000-8000-000000701001", settings)
		if err == nil || err.Error() != want {
			t.Errorf("%s: got %v, want %q", params, err, want)
		}
	}
	// An out-of-range integer folds to an always-false WHERE clause: the query
	// runs normally and simply matches no row.
	planned, err := PlanReportTool("query_data", reportParams(t,
		`{"model_name":"Course","filters":{"start_date__month":99999999999999999999}}`),
		"a1000000-0000-4000-8000-000000701001", settings)
	if err != nil {
		t.Fatalf("out-of-range month: %v", err)
	}
	if len(planned.Statements) == 0 || !strings.Contains(planned.Statements[len(planned.Statements)-1].SQL, "FALSE") {
		t.Errorf("out-of-range month: got %+v, want a statement containing FALSE", planned.Statements)
	}
}

func TestReportToolLimitsAndOffsets(t *testing.T) {
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
	}{
		{`{"model_name":"Course","fields":["title"],"limit":true}`, []string{"LIMIT 1"}},
		{`{"model_name":"Course","fields":["title"],"limit":1.5}`, []string{"LIMIT 1"}},
		{`{"model_name":"Course","fields":["title"],"limit":1000000000000000000000000000000}`, []string{"LIMIT 5000"}},
		{`{"model_name":"Course","fields":["title"],"offset":true}`, []string{"LIMIT 5000", "OFFSET 1"}},
		{`{"model_name":"Course","fields":["title"],"offset":1.5}`, []string{"LIMIT 5000", "OFFSET 1"}},
		{`{"model_name":"Course","fields":["title"],"offset":1000000000000000000000000000000}`,
			[]string{"LIMIT 5000", "OFFSET 1000000000000000000000000000000"}},
	} {
		planned := plan(t, tc.params)
		query := planned.Statements[1].SQL
		for _, part := range tc.sql {
			if !strings.Contains(query, part) {
				t.Errorf("%s: query %q lacks %q", tc.params, query, part)
			}
		}
	}
	for params, want := range map[string]string{
		`{"model_name":"Course","fields":["title"],"offset":"1"}`:  `can only concatenate str (not "int") to str`,
		`{"model_name":"Course","fields":["title"],"offset":null}`: `unsupported operand type(s) for +: 'NoneType' and 'int'`,
	} {
		_, err := PlanReportTool("query_data", reportParams(t, params), "a1000000-0000-4000-8000-000000701001", settings)
		if err == nil || err.Error() != want {
			t.Errorf("%s: got %v, want %q", params, err, want)
		}
	}
	fieldPlan, err := PlanReportTool("get_field_values", reportParams(t,
		`{"model_name":"Course","field_name":"title","limit":true}`), "a1000000-0000-4000-8000-000000701001", settings)
	if err != nil {
		t.Fatal(err)
	}
	if query := fieldPlan.Statements[1].SQL; !strings.Contains(query, "LIMIT 1") {
		t.Errorf("field values limit: query %q lacks LIMIT 1", query)
	}
	if _, err := PlanReportTool("get_field_values", reportParams(t,
		`{"model_name":"Course","field_name":"title","limit":"1"}`), "a1000000-0000-4000-8000-000000701001", settings); err == nil ||
		err.Error() != "'<' not supported between instances of 'str' and 'int'" {
		t.Errorf("field values string limit: got %v", err)
	}
}

func TestReportToolRepeatedFields(t *testing.T) {
	settings := func() (ReportSettings, error) { return ReportSettings{MaxResults: 5000, QueryTimeout: 10}, nil }
	plan, err := PlanReportTool("query_data", reportParams(t,
		`{"model_name":"Course","fields":["title","fee","title"],"order_by":["title"]}`),
		"a1000000-0000-4000-8000-000000701001", settings)
	if err != nil {
		t.Fatal(err)
	}
	query := plan.Statements[1].SQL
	if count := strings.Count(query, `"application_course"."title" AS "c`); count != 1 {
		t.Errorf("query %q selects title %d times, want 1", query, count)
	}
	if count := strings.Count(query, `"application_course"."fee" AS "c`); count != 1 {
		t.Errorf("query %q selects fee %d times, want 1", query, count)
	}
	grouped, err := PlanReportTool("aggregate_data", reportParams(t,
		`{"model_name":"Payment","aggregations":[{"function":"sum","field":"amount"}],"group_by":["type","type"]}`),
		"a1000000-0000-4000-8000-000000701001", settings)
	if err != nil {
		t.Fatal(err)
	}
	query = grouped.Statements[0].SQL
	if count := strings.Count(query, `"application_payment"."type" ASC`); count != 1 {
		t.Errorf("query %q orders by type %d times, want 1", query, count)
	}
}

func TestReportToolFieldNameTypes(t *testing.T) {
	settings := func() (ReportSettings, error) { return ReportSettings{MaxResults: 5000, QueryTimeout: 10}, nil }
	for params, want := range map[string]string{
		`{"model_name":"Course","field_name":5}`:         "'int' object has no attribute 'split'",
		`{"model_name":"Course","field_name":null}`:      "'NoneType' object has no attribute 'split'",
		`{"model_name":"Course","field_name":["title"]}`: "'list' object has no attribute 'split'",
	} {
		_, err := PlanReportTool("get_field_values", reportParams(t, params), "a1000000-0000-4000-8000-000000701001", settings)
		if err == nil || err.Error() != want {
			t.Errorf("%s: got %v, want %q", params, err, want)
		}
	}
}

func TestReportToolJoins(t *testing.T) {
	settings := func() (ReportSettings, error) { return ReportSettings{MaxResults: 5000, QueryTimeout: 10}, nil }
	for params, want := range map[string]string{
		`{"model_name":"Course","fields":["title"],"joins":[5]}`:           "'int' object has no attribute 'split'",
		`{"model_name":"Course","fields":["title"],"joins":[[1]]}`:         "'list' object has no attribute 'split'",
		`{"model_name":"Course","fields":["title"],"joins":[{"a":1}]}`:     "'dict' object has no attribute 'split'",
		`{"model_name":"Course","fields":["title"],"joins":[true]}`:        "'bool' object has no attribute 'split'",
		`{"model_name":"Course","fields":["title"],"joins":5}`:             "'int' object is not iterable",
		`{"model_name":"Course","fields":["title"],"joins":true}`:          "'bool' object is not iterable",
		`{"model_name":"Course","fields":["title"],"joins":[null,5]}`:      "'NoneType' object has no attribute 'split'",
		`{"model_name":"Course","fields":["title"],"joins":[null,"tags"]}`: "'NoneType' object has no attribute 'split'",
	} {
		_, err := PlanReportTool("query_data", reportParams(t, params), "a1000000-0000-4000-8000-000000701001", settings)
		if err == nil || err.Error() != want {
			t.Errorf("%s: got %v, want %q", params, err, want)
		}
	}
	for _, params := range []string{
		`{"model_name":"Course","fields":["title"],"joins":[null]}`,
		`{"model_name":"Course","fields":["title"],"joins":["title"]}`,
		`{"model_name":"Course","fields":["title"],"joins":"tags"}`,
		`{"model_name":"Course","fields":["title"],"joins":{"sport_association":1}}`,
		`{"model_name":"Course","fields":["title"],"joins":[null,"sport_association"]}`,
	} {
		if _, err := PlanReportTool("query_data", reportParams(t, params), "a1000000-0000-4000-8000-000000701001", settings); err != nil {
			t.Errorf("%s: %v", params, err)
		}
	}
}

func TestReportUnescape(t *testing.T) {
	// Expected values are Python 3.13 html.unescape outputs.
	for input, want := range map[string]string{
		"Calcio &amp; Basket": "Calcio & Basket",
		"a & b":               "a & b",
		"x&":                  "x&",
		"&lt;p&gt;":           "<p>",
		"&#39;":               "'",
		"&notit":              "¬it",
		"&copy;":              "©",
		"&#233;":              "é",
		"&#x1F600;":           "😀",
		"&NotEqualTilde;":     "≂̸",
		"&amp":                "&",
		"&#0;":                "\uFFFD",
		"&#xD800;":            "\uFFFD",
	} {
		got, err := reportUnescape(input)
		if err != nil || got != want {
			t.Errorf("%q: got %v %v", input, got, err)
		}
	}
}
