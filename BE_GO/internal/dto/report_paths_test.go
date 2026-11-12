package dto

import (
	"errors"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// The cases of BE/application/tests/test_report_paths.py: the fields and
// relations a report may read, on every path segment.

const reportTestAssociation = "a1000000-0000-4000-8000-000000701001"

func reportTestSettings() (ReportSettings, error) {
	return ReportSettings{MaxResults: 5000, QueryTimeout: 10}, nil
}

func reportTestModel(name string) *mcpModel { return mcpModelByLabel("application." + name) }

func reportTestPlan(t *testing.T, tool, params string) ReportPlan {
	t.Helper()
	planned, err := PlanMCPTool(tool, reportParams(t, params), reportTestAssociation, reportTestSettings)
	if err != nil {
		t.Fatalf("%s %s: %v", tool, params, err)
	}
	return planned
}

func TestReportPathAllowed(t *testing.T) {
	for _, tc := range []struct {
		model, path string
		want        bool
	}{
		// test_own_field_is_readable
		{"Associate", "first_name", true},
		// test_relation_chain_is_readable
		{"CourseSubscription", "subscription__associate__email", true},
		{"Associate", "subscription__coursesubscription__course__title", true},
		// test_relation_id_column_is_readable
		{"Subscription", "associate_id", true},
		// test_json_key_path_is_readable
		{"CarnetSubscription", "meta__lessons_left__gt", true},
		// test_lookup_after_relation_is_readable
		{"CourseSubscription", "course__in", true},
		// test_unknown_name_stays_readable
		{"Tags", "colour", true},
		// test_blocked_name_as_first_segment_is_not_readable
		{"Tags", "password", false},
		{"Tags", "password__icontains", false},
		{"Tags", "token", false},
		// test_json_key_after_readable_field_stays_readable
		{"CarnetSubscription", "meta__token", true},
		// test_relation_to_account_model_is_not_readable
		{"Instructor", "user__password", false},
		{"SportAssociation", "user__email", false},
		{"Subscription", "user__username", false},
		{"Payment", "user__is_superuser", false},
		{"MedicalCertificate", "user", false},
		{"Reminders", "user_id", false},
		// test_second_hop_to_account_model_is_not_readable
		{"Subscription", "associate__user__email", false},
		{"CourseSubscription", "subscription__user__password", false},
		// test_relation_to_unlisted_model_is_not_readable
		{"Subscription", "supplier__name", false},
		{"Subscription", "document_pdf", false},
		// test_carnet_account_relation_is_not_readable
		{"Carnet", "user_id", false},
		{"Carnet", "user_id_id", false},
		// test_dropped_columns_are_not_readable
		{"Instructor", "associated_user_id", false},
		{"Payment", "payment_intent_id", false},
		{"SportAssociation", "crm_user_id", false},
	} {
		model := reportTestModel(tc.model)
		if model == nil {
			t.Fatalf("%s: model metadata missing", tc.model)
		}
		if got := mcpPathAllowed(model, tc.path); got != tc.want {
			t.Errorf("%s %s: got %v, want %v", tc.model, tc.path, got, tc.want)
		}
	}
}

func TestReportPathAllowedNonStringPath(t *testing.T) {
	// is_path_allowed(model, None) is an AttributeError; the tool input type
	// check refuses a non-string path before the walk.
	_, err := PlanMCPTool("get_field_values", reportParams(t, `{"model_name":"Associate","field_name":5}`),
		reportTestAssociation, reportTestSettings)
	if !errors.Is(err, ErrReportNotPorted) {
		t.Errorf("non-string path: got %v, want ErrReportNotPorted", err)
	}
}

func TestReportFilterPaths(t *testing.T) {
	// test_filter_validation_drops_blocked_path_and_keeps_readable_one
	planned := reportTestPlan(t, "query_data", `{"model_name":"Instructor","fields":["first_name"],
		"filters":{"user__email":"a@b.c","email":"a@b.c","subscription__status_flag":2}}`)
	query := planned.Statements[1].SQL
	for _, part := range []string{`"application_instructor"."email" = $`, `"application_subscription"."status_flag" = $`} {
		if !strings.Contains(query, part) {
			t.Errorf("query %q lacks %q", query, part)
		}
	}
	if strings.Contains(query, `"application_user"."email"`) {
		t.Errorf("query %q keeps the dropped filter on user__email", query)
	}
	// A filter whose last segment is a lookup is checked on the path before it.
	planned = reportTestPlan(t, "query_data", `{"model_name":"Instructor","fields":["first_name"],
		"filters":{"user__password__startswith":"x"}}`)
	query = planned.Statements[1].SQL
	if strings.Contains(query, "password") {
		t.Errorf("query %q keeps the dropped filter on user__password", query)
	}
	for _, argument := range planned.Statements[1].Args {
		if argument == "x" {
			t.Errorf("args %#v keep the dropped value", planned.Statements[1].Args)
		}
	}
	// test_exclude_and_or_filter_paths_are_validated_the_same_way
	for _, condition := range []string{`"exclude":{"user__email":"x"}`, `"exclude":{"subscription__user__password":"x"}`,
		`"or_filters":[{"user__email":"x"}]`, `"or_filters":[{"subscription__user__password":"x"}]`} {
		planned := reportTestPlan(t, "query_data", `{"model_name":"Instructor","fields":["first_name"],`+condition+`}`)
		query := planned.Statements[1].SQL
		if strings.Contains(query, `"application_user"."email"`) || strings.Contains(query, "password") {
			t.Errorf("%s: query %q keeps a condition on the blocked path", condition, query)
		}
		for _, argument := range planned.Statements[1].Args {
			if argument == "x" {
				t.Errorf("%s: args %#v keep the blocked value", condition, planned.Statements[1].Args)
			}
		}
	}
	// test_filter_validation_drops_blocked_name_that_is_not_a_field: a blocked
	// name that is no field of the model is dropped, not walked as unknown.
	planned = reportTestPlan(t, "query_data", `{"model_name":"Tags","fields":["tag_name"],
		"filters":{"password":"x","tag_name":"a"}}`)
	query = planned.Statements[1].SQL
	if !strings.Contains(query, `"application_tags"."tag_name" = $`) {
		t.Errorf("query %q lacks the kept filter on tag_name", query)
	}
	if strings.Contains(query, "password") {
		t.Errorf("query %q keeps the dropped filter password", query)
	}
	for _, argument := range planned.Statements[1].Args {
		if argument == "x" {
			t.Errorf("args %#v keep the dropped value", planned.Statements[1].Args)
		}
	}
}

func TestReportGivenFields(t *testing.T) {
	// test_given_fields_drop_blocked_paths
	planned := reportTestPlan(t, "query_data", `{"model_name":"Instructor",
		"fields":["first_name","user__email","associated_user_id","subscription__status_flag"]}`)
	query := planned.Statements[1].SQL
	for _, part := range []string{`"application_instructor"."first_name" AS "c0"`, `"application_subscription"."status_flag" AS "c1"`} {
		if !strings.Contains(query, part) {
			t.Errorf("query %q lacks %q", query, part)
		}
	}
	for _, dropped := range []string{`AS "c2"`, "associated_user_id", `"application_user"."email"`} {
		if strings.Contains(query, dropped) {
			t.Errorf("query %q keeps the dropped field %q", query, dropped)
		}
	}
}

func TestReportDefaultFields(t *testing.T) {
	// test_default_field_list_has_no_unlisted_column
	for _, name := range []string{"SportAssociation", "Instructor"} {
		model := reportTestModel(name)
		names, explicit, err := newMCPQuery(model).queryFields(nil)
		if err != nil || !explicit {
			t.Fatalf("%s: %v %v", name, explicit, err)
		}
		if len(names) == 0 {
			t.Errorf("%s: no default fields", name)
		}
		for _, field := range names {
			if !model.reportFieldListed(field) {
				t.Errorf("%s: default field %q is not listed", name, field)
			}
		}
	}
}

func TestReportGroupByAndOrdering(t *testing.T) {
	// test_group_by_drops_blocked_path_and_keeps_readable_ones
	planned := reportTestPlan(t, "aggregate_data", `{"model_name":"Instructor",
		"group_by":["email","user__email","user","subscription__status_flag"],
		"aggregations":[{"function":"count","field":"*"}]}`)
	query := planned.Statements[0].SQL
	for _, part := range []string{`GROUP BY "application_instructor"."email"`, `"application_subscription"."status_flag"`} {
		if !strings.Contains(query, part) {
			t.Errorf("query %q lacks %q", query, part)
		}
	}
	if strings.Contains(query, `"application_user"."email"`) {
		t.Errorf("query %q keeps the dropped group_by path", query)
	}
	// test_group_by_keeps_date_part_of_readable_field
	planned = reportTestPlan(t, "aggregate_data", `{"model_name":"Payment",
		"group_by":["payment_date__year","subscription__user__email"],
		"aggregations":[{"function":"count","field":"*"}]}`)
	query = planned.Statements[0].SQL
	if !strings.Contains(query, `EXTRACT(YEAR FROM "application_payment"."payment_date"`) {
		t.Errorf("query %q lacks the payment_date__year group", query)
	}
	if strings.Contains(query, `"application_user"."email"`) {
		t.Errorf("query %q keeps the dropped group_by path", query)
	}
	// test_order_by_drops_blocked_path_and_keeps_minus_prefix
	planned = reportTestPlan(t, "query_data", `{"model_name":"Instructor","fields":["first_name"],
		"order_by":["-first_name","user__email","-subscription__status_flag","user"]}`)
	query = planned.Statements[1].SQL
	for _, part := range []string{`"application_instructor"."first_name" DESC`, `"application_subscription"."status_flag" DESC`} {
		if !strings.Contains(query, part) {
			t.Errorf("query %q lacks %q", query, part)
		}
	}
	if strings.Contains(query, `"application_user"."email"`) {
		t.Errorf("query %q keeps the dropped order_by path", query)
	}
}

func TestReportAggregations(t *testing.T) {
	// test_blocked_aggregation_field_is_skipped
	planned := reportTestPlan(t, "aggregate_data", `{"model_name":"Instructor",
		"aggregations":[{"function":"count","field":"*"},{"function":"count","field":"user__email"},
		{"function":"sum","field":"default_hourly_billing","alias":"total"}]}`)
	query := planned.Statements[0].SQL
	for _, part := range []string{`COUNT(*) AS "a0"`, `SUM(`, `AS "a1"`} {
		if !strings.Contains(query, part) {
			t.Errorf("query %q lacks %q", query, part)
		}
	}
	if strings.Contains(query, "user__email") {
		t.Errorf("query %q keeps the dropped aggregation", query)
	}
	// test_aggregation_on_readable_relation_chain_is_kept
	planned = reportTestPlan(t, "aggregate_data", `{"model_name":"CourseSubscription",
		"aggregations":[{"function":"max","field":"subscription__start_date","alias":"latest"}]}`)
	if query = planned.Statements[0].SQL; !strings.Contains(query, "MAX(") {
		t.Errorf("query %q lacks the aggregation on subscription__start_date", query)
	}
	// test_non_string_aggregation_field_raises_attribute_error: the alias
	// text is built from the field before the walk, so a non-string field is
	// refused as not ported instead of raising the AttributeError.
	_, err := PlanMCPTool("aggregate_data", reportParams(t, `{"model_name":"Instructor",
		"aggregations":[{"function":"sum","field":1}]}`), reportTestAssociation, reportTestSettings)
	if !errors.Is(err, ErrReportNotPorted) {
		t.Errorf("non-string aggregation field: got %v, want ErrReportNotPorted", err)
	}
}

func TestReportFieldValuesPath(t *testing.T) {
	// test_blocked_path_raises_before_querying
	_, err := PlanMCPTool("get_field_values", reportParams(t, `{"model_name":"Instructor","field_name":"user__email"}`),
		reportTestAssociation, reportTestSettings)
	if err == nil || !errors.Is(err, ErrReportFailed) || err.Error() != "Field 'user__email' is not allowed" {
		t.Errorf("get_field_values user__email: got %v", err)
	}
	planned := reportTestPlan(t, "get_field_values", `{"model_name":"Instructor","field_name":"email"}`)
	if len(planned.Statements) != 2 {
		t.Errorf("get_field_values email: got %d statements, want 2", len(planned.Statements))
	}
}

// reportTestNames reads the "name" of each object in a schema list.
func reportTestNames(t *testing.T, value any) []string {
	t.Helper()
	items, ok := value.([]any)
	if !ok {
		t.Fatalf("schema list: got %T", value)
	}
	names := make([]string, 0, len(items))
	for _, item := range items {
		object, ok := item.(domain.RequestObject)
		if !ok {
			t.Fatalf("schema item: got %T", item)
		}
		name, _ := object.Lookup("name")
		text, ok := name.(string)
		if !ok {
			t.Fatalf("schema item name: got %T", name)
		}
		names = append(names, text)
	}
	return names
}

func reportTestSameSet(left, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	seen := map[string]bool{}
	for _, item := range left {
		seen[item] = true
	}
	for _, item := range right {
		if !seen[item] {
			return false
		}
	}
	return true
}

func TestReportSchemaListedMembers(t *testing.T) {
	// test_schema_lists_only_readable_members
	planned, err := PlanReportTool("get_schema", reportParams(t, `{"model_name":"Instructor"}`),
		reportTestAssociation, reportTestSettings)
	if err != nil {
		t.Fatal(err)
	}
	schema, ok := planned.Value.(domain.RequestObject)
	if !ok {
		t.Fatalf("schema: got %T", planned.Value)
	}
	relations, _ := schema.Lookup("relations")
	fields, _ := schema.Lookup("fields")
	relationNames, fieldNames := reportTestNames(t, relations), reportTestNames(t, fields)
	if strings.Join(relationNames, ",") != "payment,subscription,reminders" {
		t.Errorf("relations: got %v", relationNames)
	}
	for _, name := range relationNames {
		if name == "user" {
			t.Errorf("relations keep %q", name)
		}
	}
	for _, name := range fieldNames {
		if name == "associated_user_id" {
			t.Errorf("fields keep %q", name)
		}
	}
	if !reportTestSameSet(fieldNames, *reportTestModel("Instructor").ReportFields) {
		t.Errorf("fields: got %v, want %v", fieldNames, *reportTestModel("Instructor").ReportFields)
	}
	if len(fieldNames) != len(reportTestUnique(fieldNames)) {
		t.Errorf("fields: %v has duplicates", fieldNames)
	}
}

func reportTestUnique(names []string) []string {
	seen := map[string]bool{}
	unique := make([]string, 0, len(names))
	for _, name := range names {
		if !seen[name] {
			seen[name] = true
			unique = append(unique, name)
		}
	}
	return unique
}

func TestReportSchemaRelationsPointToListedModels(t *testing.T) {
	// test_schema_relations_point_to_listed_models
	listed := map[string]bool{}
	for _, entry := range mcpWhitelist {
		listed[entry.Label] = true
	}
	for _, entry := range mcpWhitelist {
		model := mcpModelByLabel(entry.Label)
		schema, err := mcpModelSchema(model)
		if err != nil {
			t.Fatalf("%s: %v", entry.Label, err)
		}
		relations, _ := schema.Lookup("relations")
		fields, _ := schema.Lookup("fields")
		relationNames, fieldNames := reportTestNames(t, relations), reportTestNames(t, fields)
		if !reportTestSameSet(relationNames, *model.ReportRelations) {
			t.Errorf("%s relations: got %v, want %v", entry.Label, relationNames, *model.ReportRelations)
		}
		if !reportTestSameSet(fieldNames, *model.ReportFields) {
			t.Errorf("%s fields: got %v, want %v", entry.Label, fieldNames, *model.ReportFields)
		}
		for _, item := range relations.([]any) {
			object := item.(domain.RequestObject)
			related, _ := object.Lookup("related_model")
			label, ok := related.(string)
			if !ok || !listed[label] {
				name, _ := object.Lookup("name")
				t.Errorf("%s relation %v points to %v", entry.Label, name, related)
			}
		}
	}
}
