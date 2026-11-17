package importeffects

import (
	"crypto/sha256"
	"fmt"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// TestDefaultConstants pins the callable defaults copied from Python. The SHA-256
// digests are of the UTF-8 bytes of json.dumps(value, ensure_ascii=False):
//
//	>>> import json
//	>>> json.dumps(get_default_additional_sections(), ensure_ascii=False)
//	'[{"show_to_members": true, ...}]'                       # 4230 bytes
//	>>> json.dumps(get_default_configuration(), ensure_ascii=False)
//	'{"mandatory_phone": false, "mandatory_email": false, "mandatory_signature": false}'
//	>>> json.dumps(get_default_enabled_for(), ensure_ascii=False)
//	'["associate", "associate-membership", "membership"]'
//	>>> json.dumps(get_default_stripe_methods(), ensure_ascii=False)
//	'["card", "sepa_debit"]'
func TestDefaultConstants(t *testing.T) {
	cases := []struct{ text, digest string }{
		{defaultAdditionalSectionsJSON, "b93235bac05a322bce1935ddaf4fafbc63399ca6462df3dd42088e3a9564f852"},
		{defaultConfigurationJSON, "122f131d0e7f677a1e1195f820562b08e5c50137f348ee112b77f5256c5e94af"},
		{defaultEnabledForJSON, "2714dd52d06514e8395cc64428d61f622b034dc1f917d47d29780e441f09ff86"},
		{defaultStripeMethodsJSON, "e0a760fc2b72a9b3271846c62dd02846abb921691cf16af9b9e9b6f839f30ff8"},
	}
	for _, c := range cases {
		if got := fmt.Sprintf("%x", sha256.Sum256([]byte(c.text))); got != c.digest {
			t.Fatalf("default constant changed: %s", got)
		}
	}
}

// TestCallableDefaults drives every callable in plan.json. The nondeterministic
// ones are pinned through Effects.Now/NewUUID/Random.
func TestCallableDefaults(t *testing.T) {
	now := time.Date(2026, 11, 17, 9, 30, 0, 0, time.UTC)
	e := &Effects{
		Now:     func() time.Time { return now },
		NewUUID: func() string { return "11111111-2222-3333-4444-555555555555" },
		Random:  func() uint32 { return 0x0a0b0c },
	}
	field := &Field{Name: "field"}
	cases := []struct {
		name  string
		check func(Value) bool
	}{
		{"builtins.dict", func(v Value) bool { return v.Kind == JSON && len(v.J.(domain.RequestObject)) == 0 }},
		{"builtins.list", func(v Value) bool { return v.Kind == JSON && len(v.J.([]any)) == 0 }},
		{"django.utils.timezone.now", func(v Value) bool { return v.Kind == DateTime && v.T.Equal(now) }},
		{"uuid.uuid4", func(v Value) bool { return v.Kind == UUID && v.S == "11111111-2222-3333-4444-555555555555" }},
		{"application.models.balance_sheet_models.current_year", func(v Value) bool { return v.Kind == Int && v.S == "2026" }},
		{"application.models.courses_models.random_color", func(v Value) bool { return v.Kind == Str && v.S == "#0a0b0c" }},
		{"application.models.subscriptions_models.default_expires_at", func(v Value) bool {
			return v.Kind == DateTime && v.T.Equal(now.Add(30*24*time.Hour))
		}},
		{"application.models.utils.get_default_configuration", func(v Value) bool {
			return v.Kind == JSON && len(v.J.(domain.RequestObject)) == 3
		}},
		{"application.models.utils.get_default_enabled_for", func(v Value) bool {
			return v.Kind == JSON && len(v.J.([]any)) == 3
		}},
		{"application.models.utils.get_default_stripe_methods", func(v Value) bool {
			return v.Kind == JSON && len(v.J.([]any)) == 2
		}},
	}
	for _, c := range cases {
		v, err := e.callableDefault(field, c.name)
		if err != nil {
			t.Fatalf("%s: %v", c.name, err)
		}
		if !c.check(v) {
			t.Fatalf("%s: %+v", c.name, v)
		}
	}
}
