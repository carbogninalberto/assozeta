package service

import (
	"context"
	"errors"
	"strings"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"golang.org/x/text/cases"
	"golang.org/x/text/language"
)

var ErrSocialToken = errors.New("invalid social token")
var ErrSocialEmail = errors.New("social email mismatch")
var ErrSocialIdentity = errors.New("invalid social identity email")

type UnsupportedSocialBackend struct{ Value string }

func (*UnsupportedSocialBackend) Error() string { return "unsupported social backend" }

type SocialIdentity struct{ Email any }
type SocialTokenVerifier interface {
	Verify(context.Context, string, any) (SocialIdentity, bool, error)
}
type SocialLoginCandidates interface {
	VisitLoginCandidates(context.Context, bool, string, func(domain.Account) error) error
}
type SocialLogin struct {
	accounts SocialLoginCandidates
	tokens   SocialTokenVerifier
	slots    chan struct{}
}

func NewSocialLogin(accounts SocialLoginCandidates, tokens SocialTokenVerifier, concurrency int) *SocialLogin {
	if concurrency < 1 {
		panic("social login concurrency must be positive")
	}
	return &SocialLogin{accounts: accounts, tokens: tokens, slots: make(chan struct{}, concurrency)}
}

// Select is credential admission only. The existing streaming account query
// releases its connection before external verification. No password is checked
// or written; username/audit preparation, OTP and session delivery follow later.
func (s *SocialLogin) Select(ctx context.Context, identifierValue, token, backend any) (*domain.Account, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	select {
	case s.slots <- struct{}{}:
	case <-ctx.Done():
		return nil, ctx.Err()
	}
	defer func() { <-s.slots }()
	identifier, err := loginValueText(ctx, identifierValue)
	if err != nil {
		return nil, err
	}
	identifier = strings.TrimFunc(identifier, func(r rune) bool { return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f) })
	folded := cases.Fold().String(identifier)
	var selected, preferred domain.Account
	count, preferences := 0, 0
	err = s.accounts.VisitLoginCandidates(ctx, loginEmailPattern.MatchString(identifier), identifier, func(account domain.Account) error {
		if err := ctx.Err(); err != nil {
			return err
		}
		account.Password = ""
		if count < 2 {
			count++
		}
		if count == 1 {
			selected = account
		}
		if cases.Fold().String(account.Username) == folded {
			if preferences < 2 {
				preferences++
			}
			if preferences == 1 {
				preferred = account
			}
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	if count == 0 {
		return nil, ErrLoginNotFound
	}
	if preferences == 1 {
		selected = preferred
	} else if count != 1 {
		return nil, ErrLoginCredentials
	}
	provider := ""
	if name, ok := backend.(string); ok {
		switch name {
		case "google", "google-oauth2", "google-identity":
			provider = "google"
		case "apple", "apple-id":
			provider = "apple"
		}
	}
	if provider == "" {
		value, err := loginValueText(ctx, backend)
		if err != nil {
			return nil, err
		}
		return nil, &UnsupportedSocialBackend{Value: value}
	}
	identity, valid, err := s.tokens.Verify(ctx, provider, token)
	if err != nil {
		return nil, err
	}
	if err = ctx.Err(); err != nil {
		return nil, err
	}
	if !valid {
		return nil, ErrSocialToken
	}
	email, ok := identity.Email.(string)
	if !ok {
		return nil, ErrSocialIdentity
	}
	// Django lower(), not casefold(): expanding sharp-s would authorize a different
	// email. Unicode full lowercase also preserves dotted-I and final-sigma behavior.
	if cases.Lower(language.Und).String(email) != cases.Lower(language.Und).String(selected.Email) {
		return nil, ErrSocialEmail
	}
	if !selected.Active || selected.Deleted {
		return nil, ErrLoginCredentials
	}
	return &selected, nil
}
