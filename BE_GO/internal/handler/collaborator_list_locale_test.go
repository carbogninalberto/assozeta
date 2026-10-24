package handler

import (
	"net/http/httptest"
	"testing"
)

func TestCollaboratorLanguageSelection(t *testing.T) {
	for _, tc := range []struct{ header, cookie, want string }{
		{"", "", "it"},
		{"en", "", "en"},
		{"it", "", "it"},
		{"fr", "", "fr"},
		{"en;q=0.2,it;q=1", "", "it"},
		{"it;q=0.1,en;q=1", "", "en"},
		{"en;q=0,fr;q=0", "", "en"},
		{"en,fr", "", "en"},
		{"fr,en", "", "fr"},
		{"*;q=1,en;q=0.5", "", "it"},
		{"xx,fr", "", "fr"},
		{"EN-us", "", "en"},
		{"fr-CA", "", "fr"},
		{"it-CH", "", "it"},
		{"en-GB", "", "en-gb"},
		{"fr;q=1.001,en", "", "it"},
		{" fr", "", "it"},
		{"fr ", "", "it"},
		{"fr,", "", "fr"},
		{"fr;Q=0.9,en;q=0.1", "", "fr"},
		{"en", "fr", "fr"},
		{"fr", "en-US", "en"},
		{"en", "FR", "fr"},
		{"fr", "not-a-language", "fr"},
	} {
		r := httptest.NewRequest("GET", "/collaborators/list", nil)
		r.Header.Set("Accept-Language", tc.header)
		if tc.cookie != "" {
			r.Header.Set("Cookie", "django_language="+tc.cookie)
		}
		if got := collaboratorLanguage(r); got != tc.want {
			t.Fatalf("%+v got%q", tc, got)
		}
	}
}

func TestCollaboratorLanguageCookieLastValue(t *testing.T) {
	for _, tc := range []struct{ cookie, want string }{
		{"django_language=en; django_language=fr", "fr"},
		{"django_language=fr; django_language=en", "en"},
		{"django_language=en; django_language=invalid", "it"},
		{`django_language="\146\162"`, "fr"},
		{`django_language="en"; django_language=`, "it"},
	} {
		r := httptest.NewRequest("GET", "/collaborators/list", nil)
		r.Header.Set("Accept-Language", "it")
		r.Header.Set("Cookie", tc.cookie)
		if got := collaboratorLanguage(r); got != tc.want {
			t.Fatalf("%+v got %s", tc, got)
		}
	}
}
