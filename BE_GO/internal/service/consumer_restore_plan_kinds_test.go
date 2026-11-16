package service

import (
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/importeffects"
)

// TestRestorePlanKindsConverted: every field kind of the generated import plan
// (a foreign key takes its target's kind) is converted by restoreToPython, and
// every primary key and foreign key target kind by restoreIdentity, so
// errRestorePlanKind is never answered.
func TestRestorePlanKindsConverted(t *testing.T) {
	plan, err := importeffects.LoadPlan()
	if err != nil {
		t.Fatal(err)
	}
	for _, model := range plan.Models {
		for _, field := range model.Fields {
			kind, fieldType := field.Kind, field.Type
			if field.IsFK() {
				kind, fieldType = field.TargetKind, field.TargetType
				if _, err := restoreIdentity(kind, "1"); errors.Is(err, errRestorePlanKind) {
					t.Errorf("%s.%s: target kind %q", model.Name, field.Name, kind)
				}
			}
			if err := restoreToPython(kind, fieldType, true, "x"); errors.Is(err, errRestorePlanKind) {
				t.Errorf("%s.%s: kind %q", model.Name, field.Name, kind)
			}
		}
		if _, err := restoreIdentity(model.PK.Kind, "1"); errors.Is(err, errRestorePlanKind) {
			t.Errorf("%s: primary key kind %q", model.Name, model.PK.Kind)
		}
	}
}
