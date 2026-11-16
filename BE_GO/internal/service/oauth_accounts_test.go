package service

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
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
	// BaseUserManager.normalize_email: str.strip() then str.lower() of the domain.
	for value, want := range map[string]string{
		"  Ana@Bücher.EXAMPLE  ": "Ana@bücher.example",
		"a@ΟΣ":                   "a@ος", // the final sigma rule sees the whole domain
		"a@İSTANBUL.EXAMPLE":     "a@i\u0307stanbul.example",
		"a@ΣΣ.Example":           "a@σσ.example", // a following cased letter keeps the non-final sigma
	} {
		if got := normalizeEmail(value); got != want {
			t.Errorf("normalizeEmail(%q) = %q, want %q", value, got, want)
		}
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

// The stored username is oauth2_signup's str.upper() followed by
// create_user's normalize_username (NFKC). The login response and the create
// audit entry read the stored value.
func TestOAuthStoredUsername(t *testing.T) {
	for value, want := range map[string]string{
		"mario-7":  "MARIO-7",
		"straße":   "STRASSE",   // the full upper-case mapping
		"Èlia_ß.é": "ÈLIA_SS.É", // already composed: NFKC changes nothing
		"ｆｉｌｅ":     "FILE",      // full-width letters fold to ASCII
		"ﬁle":      "FILE",      // U+FB01 uppercases to "FI"
		"ＡＢＣ":      "ABC",
		"ｍａｒｉｏ７":   "MARIO7",
		"ΐ":        "\u03aa\u0301", // upper-case decomposes to IOTA+DIAERESIS+TONOS, NFKC recomposes to U+03AA U+0301
	} {
		if got := oauthStoredUsername(value); got != want {
			t.Errorf("oauthStoredUsername(%q) = %q, want %q", value, got, want)
		}
	}
}

// A social name with a NUL is not refused up front: create_user's INSERT
// refuses it after the invite (parity case signup-social-name-nul).
func TestOAuthSocialNameUnsupported(t *testing.T) {
	for text, want := range map[string]bool{"Zoë": false, "": false, "A\x00B": true} {
		if got := oauthTextUnsupported(text); got != want {
			t.Errorf("%q: got %v, want %v", text, got, want)
		}
	}
}

type oauthSignupStore struct {
	domain.OAuthAccountsStore
	invite  *domain.OAuthInvite
	deleted []string
	created []domain.OAuthNewUser
}

func (s *oauthSignupStore) Invite(context.Context, *string) (*domain.OAuthInvite, error) {
	return s.invite, nil
}
func (s *oauthSignupStore) DeleteInvite(_ context.Context, id string, _ time.Time, _ domain.LoginAuditContext) error {
	s.deleted = append(s.deleted, id)
	return nil
}
func (s *oauthSignupStore) CreateUser(_ context.Context, user domain.OAuthNewUser, _ domain.LoginAuditContext) error {
	s.created = append(s.created, user)
	return nil
}

type oauthSignupSocial struct{ identity SocialIdentity }

func (s oauthSignupSocial) Verify(context.Context, string, any) (SocialIdentity, bool, error) {
	return s.identity, true, nil
}

type oauthSignupCandidates struct{}

func (oauthSignupCandidates) VisitLoginCandidates(context.Context, bool, string, func(domain.Account) error) error {
	return nil
}

// The NUL name fails where create_user's INSERT does: the invite is already
// deleted, no account row is written and the partial account is untouched.
func TestOAuthSocialNameNULAfterInvite(t *testing.T) {
	expiration := time.Now().Add(time.Hour)
	store := &oauthSignupStore{invite: &domain.OAuthInvite{ID: "invite-1", UserID: "owner", ExpirationDate: &expiration, Role: 1}}
	accounts := NewOAuthAccounts(store, nil, nil, nil, nil, nil, nil,
		oauthSignupSocial{identity: SocialIdentity{Email: "persona@example.test", EmailVerified: true, FirstName: "A\x00B"}},
		oauthSignupCandidates{}, nil, OAuthAccountSettings{}, time.Now)
	object := domain.RequestObject{
		{Name: "email", Value: "body@example.test"}, {Name: "username", Value: "persona"},
		{Name: "collaboratorToken", Value: "invite"},
	}
	_, err := accounts.socialSignup(context.Background(), domain.ResolvedIdentity{}, object, "google", "token",
		"body@example.test", true, false, domain.LoginAuditContext{}, "en")
	if !errors.Is(err, ErrOAuthServer) {
		t.Fatalf("error = %v, want ErrOAuthServer", err)
	}
	if len(store.deleted) != 1 || store.deleted[0] != "invite-1" {
		t.Fatalf("invite deletions = %v", store.deleted)
	}
	if len(store.created) != 0 {
		t.Fatalf("account rows written: %v", store.created)
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
