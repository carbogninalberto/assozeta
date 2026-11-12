package service

import (
	"context"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type stripeConfigStub struct{ row *domain.SocialConfiguration }

func (s stripeConfigStub) SocialConfiguration(context.Context) (*domain.SocialConfiguration, error) {
	return s.row, nil
}

func TestStripeDirectCredentialsConfigured(t *testing.T) {
	const token = "gAAAAABq4UxlEUnkk7VzJb-4htMs7TK555_ZV3OA7PhevFW69mEbcTPSGlf4gdQE6LpB6CLjhu3XxP5ObatrvB-YmWGH-m10NQ=="
	row := func(selfHosted bool, settings string) *domain.SocialConfiguration {
		return &domain.SocialConfiguration{SelfHosted: selfHosted, Integrations: []byte(settings)}
	}
	full := StripeEnvironment{PublicKey: true, Secret: true}
	for name, tc := range map[string]struct {
		row  *domain.SocialConfiguration
		env  StripeEnvironment
		want bool
		err  bool
	}{
		"no-config-environment":      {nil, full, true, false},
		"environment-missing-public": {nil, StripeEnvironment{Secret: true}, false, false},
		"hosted-ignores-override":    {row(false, `{"stripe":{"settings":{"enabled":false}}}`), full, true, false},
		"no-settings-environment":    {row(true, `{"stripe":{"revision":2}}`), full, true, false},
		"override-enabled":           {row(true, `{"stripe":{"settings":{"enabled":true,"public_key":"pk","secret_key":"`+token+`"}}}`), StripeEnvironment{}, true, false},
		"override-disabled":          {row(true, `{"stripe":{"settings":{"enabled":false}}}`), full, false, false},
		"override-bad-token":         {row(true, `{"stripe":{"settings":{"enabled":true,"public_key":"pk","secret_key":"zzz"}}}`), full, false, false},
		"override-missing-public":    {row(true, `{"stripe":{"settings":{"enabled":true,"secret_key":"`+token+`"}}}`), full, false, true},
		"override-non-string-secret": {row(true, `{"stripe":{"settings":{"enabled":true,"public_key":"pk","secret_key":5}}}`), full, false, true},
	} {
		got, err := NewStripeAvailability(stripeConfigStub{tc.row}, tc.env, "test-secret").DirectCredentialsConfigured(context.Background())
		if got != tc.want || (err != nil) != tc.err || (err != nil && !errors.Is(err, ErrSocialConfiguration)) {
			t.Errorf("%s: got %v %v", name, got, err)
		}
	}
}
