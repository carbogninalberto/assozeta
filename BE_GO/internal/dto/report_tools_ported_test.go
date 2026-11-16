package dto

import (
	"encoding/json"
	"math"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// portedParams parses tool arguments with Python's json.loads, which produces
// NaN/Infinity (the float64 values the agent's tool calls can carry).
func portedParams(t *testing.T, text string) domain.RequestObject {
	t.Helper()
	parsed, err := ParsePythonJSON(text)
	if err != nil {
		t.Fatal(err)
	}
	return parsed.(domain.RequestObject)
}

func portedSettings() func() (ReportSettings, error) {
	return func() (ReportSettings, error) { return ReportSettings{MaxResults: 50, QueryTimeout: 10}, nil }
}

func sameFloat(left, right float64) bool {
	if math.IsNaN(left) || math.IsNaN(right) {
		return math.IsNaN(left) && math.IsNaN(right)
	}
	return left == right
}

const portedAssociation = "a1000000-0000-4000-8000-000000701001"

// TestReportToolNonFiniteAttributeErrors is item 2: a float64 argument value is
// Python's float, whose type name the AttributeError carries.
func TestReportToolNonFiniteAttributeErrors(t *testing.T) {
	for _, tc := range []struct{ tool, params, want string }{
		{"query_data", `{"model_name":"Tags","order_by":[NaN]}`, "'float' object has no attribute 'lstrip'"},
		{"query_data", `{"model_name":"Tags","order_by":[Infinity]}`, "'float' object has no attribute 'lstrip'"},
		{"query_data", `{"model_name":"Tags","fields":[NaN]}`, "'float' object has no attribute 'split'"},
		{"get_field_values", `{"model_name":"Tags","field_name":NaN}`, "'float' object has no attribute 'split'"},
	} {
		_, err := PlanReportTool(tc.tool, portedParams(t, tc.params), portedAssociation, portedSettings())
		if err == nil || err.Error() != tc.want {
			t.Errorf("%s %s: got %v, want %q", tc.tool, tc.params, err, tc.want)
		}
	}
}

// TestReportToolRandomOrdering is item 3: '?' is OrderBy(Random()), ordered
// RANDOM() ASC and appended (without an alias) to the DISTINCT select list in
// order-by order; counts stay unchanged and '-?' is a FieldError.
func TestReportToolRandomOrdering(t *testing.T) {
	plan := func(t *testing.T, params string) ReportPlan {
		t.Helper()
		planned, err := PlanReportTool("query_data", portedParams(t, params), portedAssociation, portedSettings())
		if err != nil {
			t.Fatalf("%s: %v", params, err)
		}
		for _, statement := range planned.Statements {
			reportBindings(t, params, statement.SQL, statement.Args)
		}
		return planned
	}
	tags := plan(t, `{"model_name":"Tags","fields":["tag_name"],"order_by":["?"]}`)
	if query := tags.Statements[1].SQL; !strings.Contains(query,
		`SELECT DISTINCT "application_tags"."tag_name" AS "c0", RANDOM() FROM "application_tags"`) ||
		!strings.Contains(query, `ORDER BY RANDOM() ASC LIMIT 50`) {
		t.Errorf("tags random: query %q", tags.Statements[1].SQL)
	}
	if strings.Contains(tags.Statements[0].SQL, "RANDOM") {
		t.Errorf("tags random: count statement changed: %q", tags.Statements[0].SQL)
	}
	// RANDOM() is appended in order-by order, after the selected columns, and a
	// following or preceding column keeps its position.
	randomFirst := plan(t, `{"model_name":"Course","fields":["status_flag"],"order_by":["?","title"]}`)
	if query := randomFirst.Statements[1].SQL; !strings.Contains(query,
		`AS "c0", RANDOM(), "application_course"."title" AS "__o1"`) ||
		!strings.Contains(query, `ORDER BY RANDOM() ASC, "application_course"."title" ASC`) {
		t.Errorf("'?', title: query %q", randomFirst.Statements[1].SQL)
	}
	randomLast := plan(t, `{"model_name":"Course","fields":["status_flag"],"order_by":["title","?"]}`)
	if query := randomLast.Statements[1].SQL; !strings.Contains(query,
		`AS "c0", "application_course"."title" AS "__o0", RANDOM()`) ||
		!strings.Contains(query, `ORDER BY "application_course"."title" ASC, RANDOM() ASC`) {
		t.Errorf("title, '?': query %q", randomLast.Statements[1].SQL)
	}
	// get_order_by's seen set keeps one RANDOM(): the select list and the ORDER
	// BY clause each hold exactly one.
	repeated := plan(t, `{"model_name":"Course","fields":["title"],"order_by":["?","?"]}`)
	if count := strings.Count(repeated.Statements[1].SQL, "RANDOM()"); count != 2 {
		t.Errorf("'?, ?': query %q holds RANDOM() %d times", repeated.Statements[1].SQL, count)
	}
	for _, params := range []string{
		`{"model_name":"Course","order_by":["-?"]}`,
		`{"model_name":"Tags","order_by":["-?"]}`,
	} {
		_, err := PlanReportTool("query_data", portedParams(t, params), portedAssociation, portedSettings())
		if err == nil || !strings.HasPrefix(err.Error(), "Cannot resolve keyword '?' into field. Choices are: ") {
			t.Errorf("%s: got %v, want the FieldError choices", params, err)
		}
	}
}

// TestReportToolNonFiniteLimitOffset is item 5: QuerySet.__getitem__ checks the
// negative bounds and then calls int() on both, with Python's float errors.
func TestReportToolNonFiniteLimitOffset(t *testing.T) {
	nanError := "cannot convert float NaN to integer"
	infinityError := "cannot convert float infinity to integer"
	negative := "Negative indexing is not supported."
	for _, tc := range []struct {
		tool, params, want string
	}{
		{"query_data", `{"model_name":"Tags","limit":NaN}`, nanError},
		{"query_data", `{"model_name":"Tags","offset":NaN}`, nanError},
		{"query_data", `{"model_name":"Tags","offset":Infinity}`, infinityError},
		{"query_data", `{"model_name":"Tags","offset":1e400}`, infinityError},
		{"query_data", `{"model_name":"Tags","offset":-Infinity}`, negative},
		{"query_data", `{"model_name":"Tags","offset":-1e400}`, negative},
		{"query_data", `{"model_name":"Tags","limit":-Infinity}`, negative},
		{"get_field_values", `{"model_name":"Tags","field_name":"tag_name","limit":NaN}`, nanError},
		{"get_field_values", `{"model_name":"Tags","field_name":"tag_name","limit":Infinity}`, infinityError},
		{"get_field_values", `{"model_name":"Tags","field_name":"tag_name","limit":-Infinity}`, negative},
		{"get_field_values", `{"model_name":"Tags","field_name":"tag_name","limit":1e400}`, infinityError},
	} {
		_, err := PlanReportTool(tc.tool, portedParams(t, tc.params), portedAssociation, portedSettings())
		if err == nil || err.Error() != tc.want {
			t.Errorf("%s %s: got %v, want %q", tc.tool, tc.params, err, tc.want)
		}
	}
	// min(inf, MAX) is MAX, so an infinite limit answers with the capped page.
	for _, params := range []string{
		`{"model_name":"Tags","limit":Infinity}`,
		`{"model_name":"Tags","limit":1e400}`,
	} {
		planned, err := PlanReportTool("query_data", portedParams(t, params), portedAssociation, portedSettings())
		if err != nil {
			t.Fatalf("%s: %v", params, err)
		}
		if query := planned.Statements[1].SQL; !strings.Contains(query, " LIMIT 50") {
			t.Errorf("%s: query %q lacks LIMIT 50", params, query)
		}
	}
}

// TestMCPNumberArithmetic is item 6: the number helpers read the parser's
// float64 values and an overflowing json.Number token as Python floats.
func TestMCPNumberArithmetic(t *testing.T) {
	nan, inf := math.NaN(), math.Inf(1)
	for _, tc := range []struct {
		value any
		want  float64
	}{
		{nan, nan},
		{inf, inf},
		{math.Inf(-1), math.Inf(-1)},
		{json.Number("1e400"), inf},
		{json.Number("-1e400"), math.Inf(-1)},
		{json.Number("1.5"), 1.5},
	} {
		number, ok := mcpPyNumberValue(tc.value)
		if !ok || !number.isFloat || !sameFloat(number.float, tc.want) {
			t.Errorf("mcpPyNumberValue(%#v) = %+v, %v; want float %v", tc.value, number, ok, tc.want)
		}
	}
	for _, tc := range []struct {
		left any
		want float64
	}{
		{nan, nan},
		{inf, inf},
		{math.Inf(-1), math.Inf(-1)},
		{json.Number("1e400"), inf},
	} {
		sum, err := mcpPyAdd(tc.left, mcpPyInt(50))
		if err != nil || !sum.isFloat || !sameFloat(sum.float, tc.want) {
			t.Errorf("mcpPyAdd(%#v, 50) = %+v, %v; want float %v", tc.left, sum, err, tc.want)
		}
	}
	// min(limit or MAX, MAX): NaN is kept, an infinity yields MAX.
	if number, err := mcpEffectiveLimit(nan, 50); err != nil || !number.isFloat || !math.IsNaN(number.float) {
		t.Errorf("mcpEffectiveLimit(NaN, 50) = %+v, %v", number, err)
	}
	if number, err := mcpEffectiveLimit(inf, 50); err != nil || number.isFloat || number.integer.String() != "50" {
		t.Errorf("mcpEffectiveLimit(inf, 50) = %+v, %v", number, err)
	}
	// The slice converts both bounds once the negative check passed.
	for _, tc := range []struct {
		params domain.RequestObject
		want   string
	}{
		{domain.RequestObject{{Name: "limit", Value: nan}}, "cannot convert float NaN to integer"},
		{domain.RequestObject{{Name: "offset", Value: inf}}, "cannot convert float infinity to integer"},
		{domain.RequestObject{{Name: "offset", Value: json.Number("1e400")}}, "cannot convert float infinity to integer"},
		{domain.RequestObject{{Name: "offset", Value: math.Inf(-1)}}, "Negative indexing is not supported."},
	} {
		if _, err := mcpSlice(tc.params, 50); err == nil || err.Error() != tc.want {
			t.Errorf("mcpSlice(%+v) error = %v, want %q", tc.params, err, tc.want)
		}
	}
}

// TestMCPChoiceLabels is item 8: _build_choice_map walks Payment's meta JSON key
// path to Payment.type, so a stored meta.type list or object reaches Python's
// unhashable `raw in mapping`.
func TestMCPChoiceLabels(t *testing.T) {
	labels := mcpChoiceMap(mcpModelByLabel("application.Payment"), []string{"meta__type"})
	if mapping, present := labels["meta__type"]; !present || mapping == nil {
		t.Fatalf("meta__type has no choice mapping: %#v", labels)
	}
	row := domain.RequestObject{{Name: "meta__type", Value: "cash"}}
	if err := mcpApplyLabels(row, labels); err != nil || row[0].Value != "Contanti" {
		t.Errorf("meta__type cash: %#v, %v", row, err)
	}
	for _, tc := range []struct {
		value any
		want  string
	}{
		{[]any{json.Number("1")}, "unhashable type: 'list'"},
		{domain.RequestObject{{Name: "type", Value: "cash"}}, "unhashable type: 'dict'"},
	} {
		row := domain.RequestObject{{Name: "meta__type", Value: tc.value}}
		err := mcpApplyLabels(row, labels)
		if err == nil || err.Error() != tc.want {
			t.Errorf("meta__type %#v: got %v, want %q", tc.value, err, tc.want)
		}
	}
}

// TestMCPSchemaNonFiniteModelName is item 9: str(model_name) of a float.
func TestMCPSchemaNonFiniteModelName(t *testing.T) {
	for _, tc := range []struct{ params, want string }{
		{`{"model_name":NaN}`, "Model nan not found"},
		{`{"model_name":Infinity}`, "Model inf not found"},
		{`{"model_name":-Infinity}`, "Model -inf not found"},
		{`{"model_name":1e400}`, "Model inf not found"},
		{`{"model_name":1.5}`, "Model 1.5 not found"},
	} {
		planned, err := PlanReportTool("get_schema", portedParams(t, tc.params), portedAssociation, portedSettings())
		if err != nil {
			t.Fatalf("%s: %v", tc.params, err)
		}
		if planned.Immediate == nil || planned.Immediate.Message != tc.want {
			t.Errorf("%s: got %+v, want %q", tc.params, planned.Immediate, tc.want)
		}
	}
}
