package dto

import "testing"

func TestPythonTitleAndAuditModels(t *testing.T) {
	for in, want := range map[string]string{"associatetutorrelation": "Associatetutorrelation", "oauth2app": "Oauth2App", "a b_c": "A B_C", "élan": "Élan"} {
		if got := PythonTitle(in); got != want {
			t.Errorf("%q: got %q want %q", in, got, want)
		}
	}
	got := AuditModelsFrom([]string{"user", "carnet", "zeta_model"})
	if got[0].Label != "Carnet" || got[1].Label != "Utente" || got[2].Label != "Zeta Model" {
		t.Errorf("unexpected %+v", got)
	}
}
