package service

import (
	"context"
	"errors"
	"regexp"
	"strings"
	"unicode"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	passwordcrypto "github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto"
	"golang.org/x/text/cases"
)

type LoginPasswordHasher interface {
	CheckLoginValue(context.Context, passwordcrypto.LoginPasswordValue, string) (passwordcrypto.LoginPasswordDecision, error)
	Encode(context.Context, string) (string, error)
}

// PasswordSelection is not an authenticated session. Account still requires
// username/audit normalization, OTP, response construction and login side effects.
type PasswordSelection struct {
	Account *domain.Account
	Failure string
}
type PasswordLogin struct {
	store  domain.PasswordLoginStore
	hashes LoginPasswordHasher
	slots  chan struct{}
}

func NewPasswordLogin(store domain.PasswordLoginStore, hashes LoginPasswordHasher, concurrency int) *PasswordLogin {
	if concurrency < 1 {
		panic("password login concurrency must be positive")
	}
	return &PasswordLogin{store: store, hashes: hashes, slots: make(chan struct{}, concurrency)}
}

var loginEmailPattern = regexp.MustCompile(`^[^@]+@[^@]+\.[^@]+`)

func (s *PasswordLogin) Select(ctx context.Context, identifierValue, passwordValue any) (PasswordSelection, error) {
	result := PasswordSelection{}
	if err := ctx.Err(); err != nil {
		return result, err
	}
	select {
	case s.slots <- struct{}{}:
	case <-ctx.Done():
		return result, ctx.Err()
	}
	defer func() { <-s.slots }()
	identifier, err := loginValueText(ctx, identifierValue)
	if err != nil {
		return result, err
	}
	for attempt := 0; attempt < 3; attempt++ {
		result, err := s.selectAttempt(ctx, identifier, passwordValue)
		if !errors.Is(err, domain.ErrCredentialConflict) {
			return result, err
		}
	}
	return PasswordSelection{}, domain.ErrCredentialConflict
}
func (s *PasswordLogin) selectAttempt(ctx context.Context, identifier string, passwordValue any) (PasswordSelection, error) {
	result := PasswordSelection{}
	if err := ctx.Err(); err != nil {
		return result, err
	}
	identifier = strings.TrimFunc(identifier, func(r rune) bool { return unicode.IsSpace(r) || (r >= 0x1c && r <= 0x1f) })
	byEmail := loginEmailPattern.MatchString(identifier)
	folded := cases.Fold().String(identifier)
	tx, err := s.store.BeginPasswordLogin(ctx)
	if err != nil {
		return result, err
	}
	defer tx.Rollback()
	var selected, preferred domain.Account
	var selectedLegacy, preferredLegacy bool
	candidates, matches, preferences := false, 0, 0
	var password passwordcrypto.LoginPasswordValue
	passwordReady := false
	after := ""
	for {
		page, err := tx.Candidates(ctx, byEmail, identifier, after)
		if err != nil {
			return result, err
		}
		upgrades := make([]domain.PasswordReplacement, 0, len(page))
		for _, account := range page {
			candidates = true
			if !passwordReady {
				text, err := loginValueText(ctx, passwordValue)
				if err != nil {
					return result, err
				}
				_, isString := passwordValue.(string)
				password = passwordcrypto.LoginPasswordValue{Text: text, String: isString, Null: passwordValue == nil}
				passwordReady = true
			}
			decision, err := s.hashes.CheckLoginValue(ctx, password, account.Password)
			if err != nil {
				return result, err
			}
			if !password.String && (decision.Upgrade || decision.Legacy) {
				return result, passwordcrypto.ErrLoginPasswordValue
			}
			if !decision.Match {
				continue
			}
			// Counts saturate: selection only distinguishes zero, one and many.
			if matches < 2 {
				matches++
			}
			if matches == 1 {
				selected, selectedLegacy = account, decision.Legacy
			}
			if cases.Fold().String(account.Username) == folded {
				if preferences < 2 {
					preferences++
				}
				if preferences == 1 {
					preferred, preferredLegacy = account, decision.Legacy
				}
			}
			if decision.Upgrade && !decision.Legacy {
				encoded, err := s.hashes.Encode(ctx, password.Text)
				if err != nil {
					return result, err
				}
				upgrades = append(upgrades, domain.PasswordReplacement{ID: account.ID, Encoded: encoded})
			}
		}
		if err := tx.ReplacePasswords(ctx, upgrades); err != nil {
			return result, err
		}
		if len(page) < domain.PasswordCandidatePage {
			break
		}
		after = page[len(page)-1].ID
	}
	if !candidates {
		result.Failure = "not_found"
	} else if preferences == 1 {
		selected, selectedLegacy = preferred, preferredLegacy
	} else if matches != 1 {
		result.Failure = "invalid"
	}
	if result.Failure == "" {
		if selectedLegacy {
			encoded, err := s.hashes.Encode(ctx, password.Text)
			if err != nil {
				return result, err
			}
			if err = tx.ReplacePasswords(ctx, []domain.PasswordReplacement{{ID: selected.ID, Encoded: encoded}}); err != nil {
				return result, err
			}
		}
		if !selected.Active || selected.Deleted {
			result.Failure = "invalid"
		} else {
			selected.Password = ""
			result.Account = &selected
		}
	}
	if err := ctx.Err(); err != nil {
		return PasswordSelection{}, err
	}
	if err := tx.Commit(); err != nil {
		return PasswordSelection{}, err
	}
	return result, nil
}
