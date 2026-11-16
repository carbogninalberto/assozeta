package dto

import "testing"

// TestMCPModelsInvariants asserts the shapes the AppState guards of the query
// engine rely on: every branch those comments call unreachable is checked here
// against the embedded metadata, so regenerating mcp_models.json cannot make a
// guard reachable without failing this test.
func TestMCPModelsInvariants(t *testing.T) {
	if len(mcpMeta.Models) == 0 {
		t.Fatal("no model metadata")
	}
	relationKinds := map[string]bool{"foreign_key": true, "foreign_key_reverse": true, "one_to_one": true,
		"one_to_one_reverse": true, "many_to_many": true, "many_to_many_reverse": true}
	// The reverse kinds whose local column the engine reads (the join's left
	// side and the hop's join_name in split_exclude).
	localColumnKinds := map[string]bool{"foreign_key_reverse": true, "one_to_one_reverse": true,
		"many_to_many": true, "many_to_many_reverse": true}
	for label, model := range mcpMeta.Models {
		if model.Label != label {
			t.Errorf("%s: label is %q", label, model.Label)
		}
		for _, field := range model.Fields {
			if field.Kind == "unsupported" {
				t.Errorf("%s.%s: field kind is unsupported", label, field.Name)
			}
			if field.GroupedChoices {
				t.Errorf("%s.%s: grouped choices", label, field.Name)
			}
			if field.Class == "DecimalField" && field.MaxDigits <= 0 {
				t.Errorf("%s.%s: DecimalField without max_digits", label, field.Name)
			}
			if !field.Relation {
				continue
			}
			if field.Related == "" || mcpModelByLabel(field.Related) == nil {
				t.Errorf("%s.%s: related model %q is missing", label, field.Name, field.Related)
				continue
			}
			if !relationKinds[field.Kind] {
				t.Errorf("%s.%s: relation kind %q", label, field.Name, field.Kind)
			}
			related := mcpModelByLabel(field.Related)
			if field.Kind == "foreign_key" || field.Kind == "one_to_one" {
				if related.fieldByColumn(field.TargetColumn) == nil {
					t.Errorf("%s.%s: target column %q is not a field of %s", label, field.Name, field.TargetColumn, field.Related)
				}
			}
			if localColumnKinds[field.Kind] && model.fieldByColumn(field.LocalColumn) == nil {
				t.Errorf("%s.%s: local column %q is not a field of %s", label, field.Name, field.LocalColumn, label)
			}
		}
		for _, name := range *reportList(model.ReportFields) {
			field := model.getField(name)
			if field == nil {
				t.Errorf("%s: report field %q is not a field", label, name)
				continue
			}
			if mcpKind(field.valueType()) == "" {
				t.Errorf("%s.%s: report field type %q is not mapped by mcpKind", label, name, field.valueType())
			}
		}
		for _, name := range *reportList(model.ReportRelations) {
			field := model.getField(name)
			if field == nil || !field.Relation {
				t.Errorf("%s: report relation %q is not a relation", label, name)
				continue
			}
			related := field.relatedModel()
			if related == nil {
				t.Errorf("%s.%s: report relation without metadata", label, name)
				continue
			}
			// orderExpr never expands a related model's default ordering.
			if len(related.Ordering) > 0 {
				t.Errorf("%s.%s: report relation targets %s, which has Meta.ordering %v", label, name, related.Label,
					related.Ordering)
			}
		}
	}
}

// reportList is a model's allowlist list, empty when the model has none.
func reportList(names *[]string) *[]string {
	if names == nil {
		return &[]string{}
	}
	return names
}
