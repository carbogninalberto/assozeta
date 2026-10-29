package dto

import (
	"errors"
	"reflect"
	"testing"
)

func TestModuleTemplateIDsMatchDjangoLookup(t *testing.T) {
	parse := func(raw string) any {
		value, err := ParseMessageMutationsJSON([]byte(raw))
		if err != nil {
			t.Fatal(raw, err)
		}
		return value
	}
	for raw, want := range map[string][]string{
		`{"sport_association_module_templates_ids": ["a", null, 7, true]}`: {"a", "00000000-0000-0000-0000-000000000007", "00000000-0000-0000-0000-000000000001"},
		`{"sport_association_module_templates_ids": "ab"}`:                 {"a", "b"},
		`{"sport_association_module_templates_ids": {"k": 1}}`:             {"k"},
		`{"sport_association_module_templates_ids": [null]}`:               {},
	} {
		got, err := ParseModuleTemplateIDs(parse(raw))
		if err != nil || !reflect.DeepEqual(got, want) {
			t.Errorf("%s = %#v %v, want %#v", raw, got, err, want)
		}
	}
	for raw, want := range map[string]error{
		`{}`: ErrModuleTemplatesNoInstances, `{"sport_association_module_templates_ids": []}`: ErrModuleTemplatesNoInstances,
		`{"sport_association_module_templates_ids": 0}`: ErrModuleTemplatesNoInstances, `{"sport_association_module_templates_ids": ""}`: ErrModuleTemplatesNoInstances,
		`{"sport_association_module_templates_ids": false}`: ErrModuleTemplatesNoInstances, `{"sport_association_module_templates_ids": {}}`: ErrModuleTemplatesNoInstances,
		`{"sport_association_module_templates_ids": 5}`: ErrModuleTemplateServer, `{"sport_association_module_templates_ids": true}`: ErrModuleTemplateServer,
		`{"sport_association_module_templates_ids": [1.5]}`: ErrModuleTemplateServer, `{"sport_association_module_templates_ids": [-1]}`: ErrModuleTemplateServer,
		`{"sport_association_module_templates_ids": [[1]]}`: ErrModuleTemplateServer, `[]`: ErrModuleTemplateServer, `null`: ErrModuleTemplateServer,
	} {
		if _, err := ParseModuleTemplateIDs(parse(raw)); !errors.Is(err, want) {
			t.Errorf("%s = %v, want %v", raw, err, want)
		}
	}
}

func TestModuleTemplateWriteMatchesSerializer(t *testing.T) {
	body, err := ParseMessageMutationsJSON([]byte(`{"name": " N ", "footer": "off", "template": null, "custom_header": 3}`))
	if err != nil {
		t.Fatal(err)
	}
	got, err := ParseModuleTemplateWrite(body, false, "assoc")
	if err != nil || len(got.Errors) != 0 || got.Row.Name != "N" || got.Row.Footer || !got.Row.Header || got.Row.Template != nil ||
		got.Row.CustomHeader == nil || *got.Row.CustomHeader != "3" || got.Row.AssociationID != "assoc" {
		t.Fatalf("%#v %v", got, err)
	}
	if _, err := ParseModuleTemplateWrite(body, true, "assoc"); !errors.Is(err, ErrModuleTemplateServer) {
		t.Fatal("form bodies are immutable", err)
	}
}
