package service

import (
	"encoding/json"
	"testing"
)

func TestOAuthDisposable(t *testing.T) {
	for email, want := range map[string]bool{
		"persona@yopmail.com":        true,
		"persona@mail.yopmail.com":   true,
		"persona@yopmailxcom":        true, // entries are regular expressions: '.' matches any character
		"persona@mailcatch.anything": true, // 'mailcatch.*'
		"persona@golee.it":           true, // the project's own entries
		"persona@example.test":       false,
		"yopmail.com@example.test":   false,
	} {
		if got := oauthDisposable.MatchString(email); got != want {
			t.Errorf("%s: got %v, want %v", email, got, want)
		}
	}
}

func TestOAuthSignupValues(t *testing.T) {
	if got := normalizeEmail("Nuovo.Atleta@Example.TEST"); got != "Nuovo.Atleta@example.test" {
		t.Fatal(got)
	}
	if got := normalizeEmail("a@b@C.it"); got != "a@b@c.it" {
		t.Fatal(got)
	}
	for value, want := range map[any]bool{nil: true, true: true, "not-a-uuid": false, "b2000000-0000-4000-8000-000000000001": true,
		json.Number("5"): true, json.Number("-1"): false, json.Number("1.5"): false} {
		if got := transferTokenValid(value); got != want {
			t.Errorf("%v: got %v, want %v", value, got, want)
		}
	}
	for value, want := range map[any]bool{nil: true, false: true, "": true, json.Number("0"): true, json.Number("0.0"): true,
		"x": false, json.Number("2"): false} {
		if got := pythonFalsy(value); got != want {
			t.Errorf("%v: got %v, want %v", value, got, want)
		}
	}
}

func TestSocialIdentityVerifiedEmail(t *testing.T) {
	for _, tc := range []struct {
		email, verified any
		want            bool
	}{
		{"person@example.test", true, true},
		{"person@example.test", "true", true}, // Apple sends the claim as text or as a boolean
		{"person@example.test", false, false},
		{"person@example.test", "false", false},
		{"person@example.test", "True", false},
		{"person@example.test", nil, false},
		{"person@example.test", json.Number("1"), false},
		{"person@example.test", float64(1), false},
		{"", true, false},
		{nil, true, false},
		{json.Number("17"), true, false},
	} {
		email, got := SocialIdentity{Email: tc.email, EmailVerified: tc.verified}.VerifiedEmail()
		if got != tc.want || (got && email != tc.email) {
			t.Errorf("%v / %v: got %v, want %v", tc.email, tc.verified, got, tc.want)
		}
	}
}
