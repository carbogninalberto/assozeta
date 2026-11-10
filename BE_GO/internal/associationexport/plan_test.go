package associationexport

import "testing"

// The archive keeps account and mail-server credentials out: no model
// writes a field its exclusions name, and the export never writes them.
func TestPlanLeavesOutCredentialFields(t *testing.T) {
	plan, err := LoadPlan()
	if err != nil {
		t.Fatal(err)
	}
	never := map[string][]string{
		"User":                       {"password", "two_fa_secret", "stripe_account_id", "integration_google_state", "integration_google_credentials"},
		"CommunicationConfiguration": {"email_smtp_password"},
	}
	seen := map[string]bool{}
	for _, model := range plan.Models {
		seen[model.Name] = true
		left := map[string]bool{}
		for _, name := range plan.Excluded[model.Name] {
			left[name] = true
		}
		for _, name := range never[model.Name] {
			if !left[name] {
				t.Errorf("%s.%s is not left out", model.Name, name)
			}
		}
		for _, field := range model.Fields {
			if left[field.Key] || left[field.Column] {
				t.Errorf("%s writes %s", model.Name, field.Key)
			}
		}
	}
	for name := range never {
		if !seen[name] {
			t.Errorf("%s is not exported", name)
		}
	}
}
