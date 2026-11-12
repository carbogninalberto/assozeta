package dto

import (
	"errors"
	"fmt"
	"strconv"
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

// reportBindings checks that a statement's placeholders are contiguous from $1
// and that every argument is referenced (a discarded intermediate key
// transform must not leave an unbound parameter behind).
func reportBindings(t *testing.T, label, sql string, args []any) {
	t.Helper()
	seen := map[int]bool{}
	for i := 0; i < len(sql); i++ {
		if sql[i] != '$' {
			continue
		}
		j := i + 1
		for j < len(sql) && sql[j] >= '0' && sql[j] <= '9' {
			j++
		}
		if j == i+1 {
			continue
		}
		index, err := strconv.Atoi(sql[i+1 : j])
		if err != nil {
			continue
		}
		seen[index] = true
		i = j - 1
	}
	for index := 1; index <= len(args); index++ {
		if !seen[index] {
			t.Errorf("%s: placeholder $%d is not used (args %#v)", label, index, args)
		}
	}
	for index := range seen {
		if index > len(args) {
			t.Errorf("%s: placeholder $%d has no argument (args %#v)", label, index, args)
		}
	}
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
		{"query_data", `{"model_name": "Tags", "exclude": {"subscription__status_flag": 1}}`, nil},
		{"query_data", `{"model_name": "Tags", "filters": {"tag_name__regex": "x"}}`, nil},
		{"query_data", `{"model_name": "Tags", "filters": {"tag_id": "nope"}}`, ErrReportFailed},
		{"query_data", `{"model_name": "Tags", "order_by": ["?"]}`, ErrReportNotPorted},
		{"query_data", `{"model_name": "Tags", "filters": {"creation_date__minute": 1}}`, nil},
		{"query_data", `{"model_name": "Tags", "limit": true}`, nil},
		{"query_data", `{"model_name": "Tags", "limit": "5"}`, ErrReportFailed},
		{"query_data", `{"model_name": "Carnet", "filters": {"meta__colore__icontains": "blu"}}`, nil},
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
	// EmptyResultSet/FullResultSet are handled per WhereNode: an empty OR
	// branch is dropped, an empty exclude node becomes full (no clause), and
	// a full child of a negated node empties it.
	for _, tc := range []struct {
		params string
		sql    []string
		absent []string
	}{
		{`{"model_name":"Course","fields":["title"],"or_filters":[{"start_date__month":99999999999999999999},{"title":"Nuoto Adulti"}],"order_by":["title"]}`,
			[]string{`"application_course"."title" = $`}, []string{"FALSE"}},
		{`{"model_name":"Course","fields":["title"],"exclude":{"start_date__month":99999999999999999999}}`,
			nil, []string{"FALSE", `EXTRACT(MONTH`}},
		{`{"model_name":"Course","fields":["title"],"exclude":{"status_flag__gt":-99999999999}}`,
			[]string{"FALSE"}, nil},
		{`{"model_name":"Course","fields":["title"],"or_filters":[{"start_date__month":99999999999999999999},{"start_date__month":-99999999999999999999}]}`,
			[]string{"FALSE"}, nil},
	} {
		planned, err := PlanMCPTool("query_data", reportParams(t, tc.params), "a1000000-0000-4000-8000-000000701001", settings)
		if err != nil {
			t.Fatalf("%s: %v", tc.params, err)
		}
		query := planned.Statements[len(planned.Statements)-1].SQL
		for _, part := range tc.sql {
			if !strings.Contains(query, part) {
				t.Errorf("%s: query %q lacks %q", tc.params, query, part)
			}
		}
		for _, part := range tc.absent {
			if strings.Contains(query, part) {
				t.Errorf("%s: query %q contains %q", tc.params, query, part)
			}
		}
	}
	if err := plan(t, `{"model_name":"Course","fields":["title"],"or_filters":[{"title":"Nuoto Adulti"}]}`); err != nil {
		t.Fatalf("plain or_filters: %v", err)
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

func TestReportToolJSONLookups(t *testing.T) {
	settings := func() (ReportSettings, error) { return ReportSettings{MaxResults: 5000, QueryTimeout: 10}, nil }
	plan := func(t *testing.T, params string) ReportPlan {
		t.Helper()
		planned, err := PlanReportTool("query_data", reportParams(t, params), "a1000000-0000-4000-8000-000000701001", settings)
		if err != nil {
			t.Fatalf("%s: %v", params, err)
		}
		return planned
	}
	meta := `"application_payment"."meta"`
	for _, tc := range []struct {
		params string
		sql    []string
		args   []any
	}{
		// Whole-column lookups: JSONField's own lookups.
		{`{"model_name":"Payment","filters":{"meta__contains":{"b":1}}}`,
			[]string{meta + ` @> $`}, []any{`{"b": 1}`}},
		{`{"model_name":"Payment","filters":{"meta__contained_by":{"b":1,"a":[1.0,"x & y"]}}}`,
			[]string{meta + ` <@ $`}, []any{`{"b": 1, "a": [1.0, "x & y"]}`}},
		{`{"model_name":"Payment","filters":{"meta__has_keys":["a","b"]}}`,
			[]string{meta + ` ?& $`}, []any{[]string{"a", "b"}}},
		{`{"model_name":"Payment","filters":{"meta__has_any_keys":["zz","b"]}}`,
			[]string{meta + ` ?| $`}, []any{[]string{"zz", "b"}}},
		{`{"model_name":"Payment","filters":{"meta__has_keys":"ab"}}`,
			[]string{meta + ` ?& $`}, []any{[]string{"a", "b"}}},
		{`{"model_name":"Payment","filters":{"meta__has_key":"b"}}`,
			[]string{meta + ` ? $`}, []any{"b"}},
		// A value of another type reaches PostgreSQL, which reports the
		// operator error; the parameter carries the type it had.
		{`{"model_name":"Payment","filters":{"meta__has_key":5}}`,
			[]string{meta + ` ? $`, `::int`}, []any{int64(5)}},
		{`{"model_name":"Payment","filters":{"meta__has_key":["a"]}}`,
			[]string{meta + ` ? $`, `::text[]`}, []any{[]string{"a"}}},
		{`{"model_name":"Payment","filters":{"meta":{"b":1}}}`,
			[]string{meta + ` = $`}, []any{`{"b": 1}`}},
		{`{"model_name":"Payment","filters":{"meta":"x"}}`,
			[]string{meta + ` = $`}, []any{`"x"`}},
		{`{"model_name":"Payment","filters":{"meta__exact":null}}`,
			[]string{meta + ` = $`}, []any{"null"}},
		{`{"model_name":"Payment","filters":{"meta__gt":1}}`,
			[]string{meta + ` > $`}, []any{"1"}},
		{`{"model_name":"Payment","filters":{"meta__in":[{"b":1}]}}`,
			[]string{meta + ` IN ($`}, []any{`{"b": 1}`}},
		{`{"model_name":"Payment","filters":{"meta__icontains":"OK"}}`,
			[]string{`UPPER(` + meta + `::text) LIKE UPPER($`}, []any{"%OK%"}},
		{`{"model_name":"Payment","filters":{"meta__startswith":"{"}}`,
			[]string{meta + `::text LIKE $`}, []any{"{%"}},
		// Key transforms, at any depth.
		{`{"model_name":"Payment","filters":{"meta__a__0":1.0}}`,
			[]string{`(` + meta + ` #> $`, `::text[]`}, []any{[]string{"a", "0"}, "1.0"}},
		{`{"model_name":"Payment","filters":{"meta__0":1}}`,
			[]string{`(` + meta + ` -> $`, `::int`}, []any{int64(0), "1"}},
		{`{"model_name":"Payment","filters":{"meta__-1":1}}`,
			[]string{`(` + meta + ` -> $`, `::int`}, []any{int64(-1)}},
		{`{"model_name":"Payment","filters":{"meta__b":null}}`,
			[]string{`(` + meta + ` -> $`, `) = $`}, []any{"b", "null"}},
		{`{"model_name":"Payment","filters":{"meta__b__isnull":true}}`,
			[]string{`(` + meta + ` -> $`, `) IS NULL`}, nil},
		{`{"model_name":"Payment","filters":{"meta__b__isnull":false}}`,
			[]string{`(` + meta + ` -> $`, `) IS NOT NULL`}, nil},
		{`{"model_name":"Payment","filters":{"meta__b__in":[1,2]}}`,
			[]string{`(` + meta + ` -> $`, `) IN ($`}, []any{"1", "2"}},
		{`{"model_name":"Payment","filters":{"meta__b__range":[0,2]}}`,
			[]string{`(` + meta + ` -> $`, `) BETWEEN $`}, []any{"0", "2"}},
		{`{"model_name":"Payment","filters":{"meta__b__gt":0}}`,
			[]string{`(` + meta + ` -> $`, `) > $`}, []any{"0"}},
		{`{"model_name":"Payment","filters":{"meta__a__contains":[1.0]}}`,
			[]string{`(` + meta + ` -> $`, `) @> $`}, []any{"[1.0]"}},
		{`{"model_name":"Payment","filters":{"meta__a__has_key":"x"}}`,
			[]string{`(` + meta + ` -> $`, `) ? $`}, []any{"x"}},
		{`{"model_name":"Payment","filters":{"meta__note__year":1}}`,
			[]string{`(` + meta + ` #> $`, `) = $`}, []any{[]string{"note", "year"}, "1"}},
		{`{"model_name":"Payment","filters":{"meta__note__len":2}}`,
			[]string{`(` + meta + ` #> $`, `) = $`}, []any{[]string{"note", "len"}}},
		{`{"model_name":"Payment","filters":{"meta__note__icontains":"O"}}`,
			[]string{`UPPER((` + meta + ` ->> $`, `)::text) LIKE UPPER($`}, []any{"note", "%O%"}},
		{`{"model_name":"Payment","filters":{"meta__note__startswith":"o"}}`,
			[]string{`(` + meta + ` ->> $`, `)::text LIKE $`}, []any{"o%"}},
		{`{"model_name":"Payment","filters":{"meta__note__iexact":"OK"}}`,
			[]string{`UPPER((` + meta + ` ->> $`, `)::text) = UPPER($`}, []any{"note"}},
		{`{"model_name":"Payment","filters":{"meta__note__regex":"^o"}}`,
			[]string{`(` + meta + ` ->> $`, `)::text ~ $`}, []any{"^o"}},
		{`{"model_name":"Payment","filters":{"meta__note__in":["ok",1]}}`,
			[]string{`(` + meta + ` -> $`, `) IN ($`}, []any{`"ok"`, "1"}},
		{`{"model_name":"Payment","filters":{"meta__it's":1}}`,
			[]string{`(` + meta + ` -> $`, `) = $`}, []any{"it's"}},
		{`{"model_name":"Payment","filters":{"meta__a\\b":1}}`,
			[]string{`(` + meta + ` -> $`, `) = $`}, []any{`a\b`}},
		// Key paths in values(), ordering, grouping and get_field_values.
		{`{"model_name":"Payment","fields":["amount","meta__a__0","meta__note"],"order_by":["amount"]}`,
			[]string{`(` + meta + ` #> $`, `) AS "c1"`, `(` + meta + ` -> $`, `) AS "c2"`}, nil},
		{`{"model_name":"Payment","fields":["amount"],"order_by":["meta__a__0","amount"]}`,
			[]string{`ORDER BY (` + meta + ` #> $`, `) ASC`}, nil},
	} {
		planned := plan(t, tc.params)
		query := planned.Statements[1].SQL
		for _, statement := range planned.Statements {
			reportBindings(t, tc.params, statement.SQL, statement.Args)
		}
		for _, part := range tc.sql {
			if !strings.Contains(query, part) {
				t.Errorf("%s: query %q lacks %q", tc.params, query, part)
			}
		}
		for _, want := range tc.args {
			found := false
			for _, argument := range planned.Statements[1].Args {
				if fmt.Sprint(argument) == fmt.Sprint(want) {
					found = true
				}
			}
			if !found {
				t.Errorf("%s: args %#v lack %#v", tc.params, planned.Statements[1].Args, want)
			}
		}
	}
	// Grouping by a key path groups by the extracted value.
	grouped, err := PlanReportTool("aggregate_data", reportParams(t,
		`{"model_name":"Payment","aggregations":[{"function":"count","field":"*"}],"group_by":["meta__note"]}`),
		"a1000000-0000-4000-8000-000000701001", settings)
	if err != nil {
		t.Fatal(err)
	}
	if query := grouped.Statements[0].SQL; !strings.Contains(query, `GROUP BY (`+meta+` -> $`) {
		t.Errorf("group_by key: query %q lacks a key transform", query)
	}
	for _, statement := range grouped.Statements {
		reportBindings(t, "group_by meta__note", statement.SQL, statement.Args)
	}
	// get_field_values reads the key value and orders by it.
	values, err := PlanReportTool("get_field_values", reportParams(t,
		`{"model_name":"Payment","field_name":"meta__note"}`), "a1000000-0000-4000-8000-000000701001", settings)
	if err != nil {
		t.Fatal(err)
	}
	if query := values.Statements[1].SQL; !strings.Contains(query, `(`+meta+` -> $`) {
		t.Errorf("get_field_values key: query %q lacks a key transform", query)
	}
	for _, statement := range values.Statements {
		reportBindings(t, "get_field_values meta__note", statement.SQL, statement.Args)
	}
	// The lookups KeyTransform offers, and range on the whole column, build
	// Django's SQL instead of being refused.
	for _, tc := range []struct {
		params string
		sql    []string
	}{
		{`{"model_name":"Payment","filters":{"meta__range":[0,2]}}`,
			[]string{meta + ` BETWEEN $`, `::jsonb`}},
		{`{"model_name":"Payment","filters":{"meta__a__has_keys":["x"]}}`,
			[]string{`(` + meta + ` -> $`, `) ?& $`, `::text[]`}},
		{`{"model_name":"Payment","filters":{"meta__a__has_any_keys":["x"]}}`,
			[]string{`(` + meta + ` -> $`, `) ?| $`, `::text[]`}},
		{`{"model_name":"Payment","filters":{"meta__a__contained_by":{}}}`,
			[]string{`(` + meta + ` -> $`, `) <@ $`, `::jsonb`}},
	} {
		planned := plan(t, tc.params)
		query := planned.Statements[1].SQL
		for _, statement := range planned.Statements {
			reportBindings(t, tc.params, statement.SQL, statement.Args)
		}
		for _, part := range tc.sql {
			if !strings.Contains(query, part) {
				t.Errorf("%s: query %q lacks %q", tc.params, query, part)
			}
		}
	}
	// A has_key value the operator cannot take reaches PostgreSQL; None is
	// rejected before the statement is built.
	if _, err := PlanReportTool("query_data", reportParams(t,
		`{"model_name":"Payment","filters":{"meta__has_key":null}}`), "a1000000-0000-4000-8000-000000701001", settings); err == nil ||
		err.Error() != "Cannot use None as a query value" {
		t.Errorf("has_key None: got %v", err)
	}
}

// TestReportToolJSONLookupsR3b covers the lookups R3 still refused: range on
// the whole column, the KeyTransform lookups contained_by/has_keys/
// has_any_keys, has_key values of other types, key indexes outside int64 and
// every JSON value type in exact/contains/in.
func TestReportToolJSONLookupsR3b(t *testing.T) {
	settings := func() (ReportSettings, error) { return ReportSettings{MaxResults: 5000, QueryTimeout: 10}, nil }
	meta := `"application_payment"."meta"`
	for _, tc := range []struct {
		params string
		sql    []string
		args   []any
	}{
		{`{"model_name":"Payment","filters":{"meta__range":[null,{"z":1}]}}`,
			[]string{meta + ` BETWEEN $`, `::jsonb`}, []any{"null", `{"z": 1}`}},
		{`{"model_name":"Payment","filters":{"meta__a__range":[[1.0],[2.0]]}}`,
			[]string{`(` + meta + ` -> $`, `) BETWEEN $`, `::jsonb`}, []any{"[1.0]", "[2.0]"}},
		{`{"model_name":"Payment","filters":{"meta__a__contained_by":[1.0,"x & y"]}}`,
			[]string{`(` + meta + ` -> $`, `) <@ $`, `::jsonb`}, []any{`[1.0, "x & y"]`}},
		{`{"model_name":"Payment","filters":{"meta__a__has_keys":["x & y","1.0"]}}`,
			[]string{`(` + meta + ` -> $`, `) ?& $`, `::text[]`}, []any{[]string{"x & y", "1.0"}}},
		{`{"model_name":"Payment","filters":{"meta__a__has_any_keys":["zz","x"]}}`,
			[]string{`(` + meta + ` -> $`, `) ?| $`, `::text[]`}, []any{[]string{"zz", "x"}}},
		// has_key values keep the type psycopg2 gives them.
		{`{"model_name":"Payment","filters":{"meta__has_key":1.5}}`,
			[]string{meta + ` ? $`, `::numeric`}, []any{1.5}},
		{`{"model_name":"Payment","filters":{"meta__has_key":true}}`,
			[]string{meta + ` ? $`, `::boolean`}, []any{true}},
		{`{"model_name":"Payment","filters":{"meta__has_key":123456789012345678901234567890}}`,
			[]string{meta + ` ? $`, `::numeric`}, []any{"123456789012345678901234567890"}},
		{`{"model_name":"Payment","filters":{"meta__has_key":9223372036854775807}}`,
			[]string{meta + ` ? $`, `::bigint`}, []any{int64(9223372036854775807)}},
		{`{"model_name":"Payment","filters":{"meta__has_key":[["a"]]}}`,
			[]string{meta + ` ? $`, `::text[]`}, []any{[]string{"['a']"}}},
		{`{"model_name":"Payment","filters":{"meta__has_key":[1,2]}}`,
			[]string{meta + ` ? $`, `::int[]`}, []any{[]int32{1, 2}}},
		{`{"model_name":"Payment","filters":{"meta__has_key":[1.5,2.5]}}`,
			[]string{meta + ` ? $`, `::numeric[]`}, []any{[]string{"1.5", "2.5"}}},
		{`{"model_name":"Payment","filters":{"meta__has_key":["a",1]}}`,
			[]string{meta + ` ? $`, `::int[]`}, []any{[]string{"a", "1"}}},
		{`{"model_name":"Payment","filters":{"meta__has_key":[]}}`,
			[]string{meta + ` ? $`}, []any{"{}"}},
		{`{"model_name":"Payment","filters":{"meta__has_key":[null,null]}}`,
			[]string{meta + ` ? $`}, []any{"{NULL,NULL}"}},
		{`{"model_name":"Payment","filters":{"meta__has_keys":{"a":1,"b":2}}}`,
			[]string{meta + ` ?& $`, `::text[]`}, []any{[]string{"a", "b"}}},
		{`{"model_name":"Payment","filters":{"meta__has_keys":[["a"],1,true,null]}}`,
			[]string{meta + ` ?& $`, `::text[]`}, []any{[]string{"['a']", "1", "True", "None"}}},
		// A single key index keeps Django's int cast; outside int64 psycopg2
		// sends numeric.
		{`{"model_name":"Payment","filters":{"meta__-1":1}}`,
			[]string{`(` + meta + ` -> $`, `::int`}, []any{int64(-1)}},
		{`{"model_name":"Payment","filters":{"meta__9223372036854775807":1}}`,
			[]string{`(` + meta + ` -> $`, `::bigint`}, []any{int64(9223372036854775807)}},
		{`{"model_name":"Payment","filters":{"meta__99999999999999999999":1}}`,
			[]string{`(` + meta + ` -> $`, `::numeric`}, []any{"99999999999999999999"}},
		{`{"model_name":"Payment","filters":{"meta__a__-1":"x & y"}}`,
			[]string{`(` + meta + ` #> $`, `) = $`, `::jsonb`}, []any{[]string{"a", "-1"}}},
		// Every JSON value type in exact/contains/in is dumped with json.dumps.
		{`{"model_name":"Payment","filters":{"meta__b__exact":{"x":{"y":[1,2]}}}}`,
			[]string{`(` + meta + ` -> $`, `) = $`, `::jsonb`}, []any{`{"x": {"y": [1, 2]}}`}},
		{`{"model_name":"Payment","filters":{"meta__a__contains":[{"x":1}]}}`,
			[]string{`(` + meta + ` -> $`, `) @> $`, `::jsonb`}, []any{`[{"x": 1}]`}},
		{`{"model_name":"Payment","filters":{"meta__b__in":[1.0,{"x":[true,null]},123456789012345678901234567890]}}`,
			[]string{`(` + meta + ` -> $`, `) IN ($`}, []any{`1.0`, `{"x": [true, null]}`, "123456789012345678901234567890"}},
	} {
		planned, err := PlanReportTool("query_data", reportParams(t, tc.params), "a1000000-0000-4000-8000-000000701001", settings)
		if err != nil {
			t.Errorf("%s: %v", tc.params, err)
			continue
		}
		query := planned.Statements[1].SQL
		for _, statement := range planned.Statements {
			reportBindings(t, tc.params, statement.SQL, statement.Args)
		}
		for _, part := range tc.sql {
			if !strings.Contains(query, part) {
				t.Errorf("%s: query %q lacks %q", tc.params, query, part)
			}
		}
		for _, want := range tc.args {
			found := false
			for _, argument := range planned.Statements[1].Args {
				if fmt.Sprint(argument) == fmt.Sprint(want) {
					found = true
				}
			}
			if !found {
				t.Errorf("%s: args %#v lack %#v", tc.params, planned.Statements[1].Args, want)
			}
		}
	}
	// psycopg2 cannot adapt a dict parameter; Django reports its text before
	// the statement reaches PostgreSQL.
	for _, params := range []string{
		`{"model_name":"Payment","filters":{"meta__has_key":{"a":1}}}`,
		`{"model_name":"Payment","filters":{"meta__has_key":[{"a":1}]}}`,
	} {
		if _, err := PlanReportTool("query_data", reportParams(t, params), "a1000000-0000-4000-8000-000000701001", settings); err == nil ||
			err.Error() != "can't adapt type 'dict'" {
			t.Errorf("%s: got %v", params, err)
		}
	}
}

func TestReportToolValuePreparation(t *testing.T) {
	settings := func() (ReportSettings, error) { return ReportSettings{MaxResults: 5000, QueryTimeout: 10}, nil }
	plan := func(t *testing.T, params string) (ReportPlan, error) {
		t.Helper()
		return PlanReportTool("query_data", reportParams(t, params), "a1000000-0000-4000-8000-000000701001", settings)
	}
	for params, want := range map[string]string{
		`{"model_name":"Course","filters":{"status_flag__in":null}}`:       "'NoneType' object is not iterable",
		`{"model_name":"Course","filters":{"status_flag__range":null}}`:    "'NoneType' object is not iterable",
		`{"model_name":"Course","filters":{"status_flag__range":[1]}}`:     "tuple index out of range",
		`{"model_name":"Course","filters":{"status_flag__range":[1,2,3]}}`: "not all arguments converted during string formatting",
		`{"model_name":"Course","filters":{"status_flag__in":"abc"}}`:      "Field 'status_flag' expected a number but got 'a'.",
		`{"model_name":"Course","filters":{"status_flag__in":"12"}}`:       "'int' object is not iterable",
		`{"model_name":"Course","filters":{"status_flag__in":2}}`:          "'int' object is not iterable",
		`{"model_name":"Course","filters":{"status_flag__range":"12"}}`:    "'int' object is not iterable",
		`{"model_name":"Course","filters":{"status_flag":[2]}}`:            "Field 'status_flag' expected a number but got [2].",
		`{"model_name":"Course","filters":{"status_flag":{"a":1}}}`:        "Field 'status_flag' expected a number but got {'a': 1}.",
		`{"model_name":"Course","filters":{"fee":"inf"}}`:                  `['Il valore "inf" deve essere un numero decimale.']`,
		`{"model_name":"Course","filters":{"fee":"nan"}}`:                  `['Il valore "nan" deve essere un numero decimale.']`,
		`{"model_name":"Course","filters":{"fee":[1]}}`:                    `['Il valore "[1]" deve essere un numero decimale.']`,
		`{"model_name":"Course","filters":{"pinned":[]}}`:                  `['Il valore "[]" deve essere True oppure False.']`,
		`{"model_name":"Course","filters":{"course_id":[1]}}`:              `['"[1]" non è uno UUID valido.']`,
		`{"model_name":"Course","filters":{"course_id":1.5}}`:              `['"1.5" non è uno UUID valido.']`,
		`{"model_name":"Course","filters":{"start_date":5}}`:               "fromisoformat: argument must be str",
		`{"model_name":"Course","filters":{"start_date":true}}`:            "fromisoformat: argument must be str",
		`{"model_name":"Course","filters":{"start_date":[1]}}`:             "fromisoformat: argument must be str",
		`{"model_name":"Course","filters":{"start_date":" 2025-09-01"}}`:   `['Il valore " 2025-09-01" ha un formato non valido. Deve essere nel formato AAAA-MM-GG HH:MM[:ss[.uuuuuu]][TZ].']`,
	} {
		_, err := plan(t, params)
		if err == nil || err.Error() != want {
			t.Errorf("%s: got %v, want %q", params, err, want)
		}
	}
	for _, tc := range []struct {
		params string
		sql    string
		args   []any
	}{
		{`{"model_name":"Course","filters":{"status_flag__in":{"1":2}}}`, ` IN ($`, []any{int64(1)}},
		{`{"model_name":"Course","filters":{"status_flag__in":[2,99999999999]}}`, ` IN ($`, []any{int64(2), int64(99999999999)}},
		{`{"model_name":"Course","filters":{"title__in":"Nuoto Adulti"}}`, ` IN ($`, []any{"N", "u", "o", "t", " ", "A", "d", "l", "i"}},
		{`{"model_name":"Course","filters":{"status_flag":2.5}}`, ` = $`, []any{int64(2)}},
		{`{"model_name":"Course","filters":{"status_flag__gte":2.5}}`, ` >= $`, []any{int64(3)}},
		{`{"model_name":"Course","filters":{"status_flag__lt":2.5}}`, ` < $`, []any{int64(3)}},
		{`{"model_name":"Course","filters":{"status_flag":"٢"}}`, ` = $`, []any{int64(2)}},
		{`{"model_name":"Course","filters":{"fee":"٤٥.٥٠"}}`, ` = $`, []any{"45.5"}},
		{`{"model_name":"Course","filters":{"fee":true}}`, ` = $`, []any{"1"}},
		{`{"model_name":"Course","filters":{"course_id":5}}`, ` = $`, []any{"00000000-0000-0000-0000-000000000005"}},
		{`{"model_name":"Course","filters":{"course_id":true}}`, ` = $`, []any{"00000000-0000-0000-0000-000000000001"}},
		{`{"model_name":"Course","filters":{"course_id":"+0000000000000000000000000000000"}}`, ` = $`, []any{"00000000-0000-0000-0000-000000000000"}},
		{`{"model_name":"Course","filters":{"start_date":"20250901"}}`, ` = $`, []any{"2025-09-01T00:00:00Z"}},
		{`{"model_name":"Course","filters":{"start_date":"2025-W36-1"}}`, ` = $`, []any{"2025-09-01T00:00:00Z"}},
		{`{"model_name":"Course","filters":{"start_date__gt":"2025-09-01T08"}}`, ` > $`, []any{"2025-09-01T08:00:00Z"}},
		{`{"model_name":"Course","filters":{"title":[1]}}`, ` = $`, []any{"[1]"}},
		{`{"model_name":"Course","filters":{"title":{"a":1}}}`, ` = $`, []any{"{'a': 1}"}},
		{`{"model_name":"Course","filters":{"title__contains":[1]}}`, ` LIKE $`, []any{"%[1]%"}},
	} {
		planned, err := plan(t, tc.params)
		if err != nil {
			t.Fatalf("%s: %v", tc.params, err)
		}
		statement := planned.Statements[len(planned.Statements)-1]
		if !strings.Contains(statement.SQL, tc.sql) {
			t.Errorf("%s: query %q lacks %q", tc.params, statement.SQL, tc.sql)
		}
		for _, want := range tc.args {
			found := false
			for _, argument := range statement.Args {
				if argument == want {
					found = true
				}
			}
			if !found {
				t.Errorf("%s: args %#v lack %#v", tc.params, statement.Args, want)
			}
		}
	}
	planned, err := plan(t, `{"model_name":"Course","filters":{"status_flag__range":[null,2]}}`)
	if err != nil {
		t.Fatal(err)
	}
	statement := planned.Statements[len(planned.Statements)-1]
	if !strings.Contains(statement.SQL, " BETWEEN $") || len(statement.Args) != 3 || statement.Args[1] != nil || statement.Args[2] != int64(2) {
		t.Errorf("range with None: SQL %q args %#v", statement.SQL, statement.Args)
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

func TestReportToolSplitExclude(t *testing.T) {
	settings := func() (ReportSettings, error) { return ReportSettings{MaxResults: 5000, QueryTimeout: 10}, nil }
	plan := func(t *testing.T, tool, params string) ReportPlan {
		t.Helper()
		planned, err := PlanReportTool(tool, reportParams(t, params), "a1000000-0000-4000-8000-000000701001", settings)
		if err != nil {
			t.Fatalf("%s: %v", params, err)
		}
		for _, statement := range planned.Statements {
			reportBindings(t, params, statement.SQL, statement.Args)
		}
		return planned
	}
	for _, tc := range []struct {
		params string
		sql    []string
		args   []any
	}{
		// A many-to-many relation: the subquery starts at the through table
		// and selects its join column.
		{`{"model_name":"Subscription","fields":["subscription_number"],"exclude":{"tags__tag_name":"Agonista"}}`,
			[]string{`NOT (EXISTS(SELECT 1 AS "a" FROM "application_subscription_tags" U1`,
				`INNER JOIN "application_tags" U2 ON (U1."tags_id" = U2."tag_id")`,
				`WHERE (U2."tag_name" = $2 AND U1."subscription_id" = ("application_subscription"."subscription_id")) LIMIT 1))`},
			[]any{"Agonista"}},
		// A reverse foreign key selects the related model's foreign key.
		{`{"model_name":"Course","fields":["title"],"exclude":{"coursesubscription__paid":true}}`,
			[]string{`NOT (EXISTS(SELECT 1 AS "a" FROM "application_coursesubscription" U1 WHERE (U1."paid" AND ` +
				`U1."course_id" = ("application_course"."course_id")) LIMIT 1))`}, nil},
		// Several keys of one exclude are ANDed inside one NOT.
		{`{"model_name":"Subscription","fields":["subscription_number"],"exclude":{"tags__tag_name__startswith":"Under","status_flag":1}}`,
			[]string{`NOT ("application_subscription"."status_flag" = $2 AND EXISTS(SELECT 1 AS "a" FROM ` +
				`"application_subscription_tags" U1 INNER JOIN "application_tags" U2`, `U2."tag_name"::text LIKE $3`},
			[]any{int64(1), "Under%"}},
		// A condition that reads the relation with IS NULL keeps the outer
		// joins, so the subquery starts at the model table.
		{`{"model_name":"Subscription","fields":["subscription_number"],"exclude":{"tags":null}}`,
			[]string{`NOT (EXISTS(SELECT 1 AS "a" FROM "application_subscription" U0 LEFT OUTER JOIN ` +
				`"application_subscription_tags" U1 ON (U0."subscription_id" = U1."subscription_id") WHERE (` +
				`U1."tags_id" IS NULL AND U0."subscription_id" = ("application_subscription"."subscription_id")) LIMIT 1))`},
			nil},
		// A path that continues after the many-valued hop joins the rest of
		// the path inside the subquery.
		{`{"model_name":"Course","fields":["title"],"exclude":{"coursesubscription__subscription__tags__tag_name":"Under_14"}}`,
			[]string{`FROM "application_coursesubscription" U1 INNER JOIN "application_subscription" U2 ON (` +
				`U1."subscription_id" = U2."subscription_id") INNER JOIN "application_subscription_tags" U3 ON (` +
				`U2."subscription_id" = U3."subscription_id") INNER JOIN "application_tags" U4 ON (` +
				`U3."tags_id" = U4."tag_id") WHERE (U4."tag_name" = $2`},
			[]any{"Under_14"}},
		// The reverse direction of a many-to-many relation.
		{`{"model_name":"Tags","fields":["tag_name"],"exclude":{"subscription__status_flag":1}}`,
			[]string{`FROM "application_subscription_tags" U1 INNER JOIN "application_subscription" U2 ON (` +
				`U1."subscription_id" = U2."subscription_id")`, `U1."tags_id" = ("application_tags"."tag_id")`},
			[]any{int64(1)}},
		// An empty __in list inside the subquery matches nothing, so the
		// negation drops no row.
		{`{"model_name":"Subscription","fields":["subscription_number"],"exclude":{"tags__tag_id__in":[]}}`,
			[]string{`NOT (FALSE)`}, nil},
		// A nullable forward relation before the many-valued hop keeps its
		// outer join and adds the null check of the trimmed prefix.
		{`{"model_name":"Subscription","fields":["subscription_number"],"exclude":{"associate__subscription__tags__isnull":true}}`,
			[]string{`NOT ((EXISTS(SELECT 1 AS "a" FROM "application_associate" U1 LEFT OUTER JOIN ` +
				`"application_subscription" U2 ON (U1."associate_id" = U2."associate_id")`,
				`OR "application_subscription"."associate_id" IS NULL))`}, nil},
	} {
		planned := plan(t, "query_data", tc.params)
		query := planned.Statements[len(planned.Statements)-1].SQL
		for _, part := range tc.sql {
			if !strings.Contains(query, part) {
				t.Errorf("%s: query %q lacks %q", tc.params, query, part)
			}
		}
		for _, want := range tc.args {
			found := false
			for _, argument := range planned.Statements[len(planned.Statements)-1].Args {
				if fmt.Sprint(argument) == fmt.Sprint(want) {
					found = true
				}
			}
			if !found {
				t.Errorf("%s: args %#v lack %#v", tc.params, planned.Statements[len(planned.Statements)-1].Args, want)
			}
		}
	}
	// count_data, aggregate_data and get_field_values use the same clause.
	for _, tool := range []string{"count_data", "get_field_values", "aggregate_data"} {
		params := map[string]string{
			"count_data":       `{"model_name":"Subscription","exclude":{"tags__tag_name":"Agonista"}}`,
			"get_field_values": `{"model_name":"Subscription","field_name":"subscription_number","exclude":{"tags__tag_name":"Agonista"}}`,
			"aggregate_data": `{"model_name":"Subscription","exclude":{"tags__tag_name":"Agonista"},` +
				`"aggregations":[{"function":"count","field":"*"}]}`,
		}[tool]
		planned := plan(t, tool, params)
		for _, statement := range planned.Statements {
			if !strings.Contains(statement.SQL, `EXISTS(SELECT 1 AS "a" FROM "application_subscription_tags"`) {
				t.Errorf("%s %s: statement %q lacks the subquery", tool, params, statement.SQL)
			}
		}
	}
	// The filters of the same relation still join the outer query.
	planned := plan(t, "query_data", `{"model_name":"Subscription","fields":["subscription_number","tags__tag_name"],
		"filters":{"tags__tag_name":"Under_14"},"exclude":{"tags__tag_name":"Agonista"},"order_by":["subscription_number"]}`)
	query := planned.Statements[len(planned.Statements)-1].SQL
	if !strings.Contains(query, `INNER JOIN "application_tags" ON`) || !strings.Contains(query, `"application_tags"."tag_name" = $2`) {
		t.Errorf("filters beside exclude: query %q lacks the outer join", query)
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
