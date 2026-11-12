package service

import (
	"context"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type IdentifierAccounts interface {
	UsernameExists(context.Context, string) (bool, error)
	FirstMatchingIdentifier(context.Context, bool, string) (*domain.IdentifierAccount, error)
}
type IdentifierAvailability struct{ accounts IdentifierAccounts }

func NewIdentifierAvailability(accounts IdentifierAccounts) *IdentifierAvailability {
	return &IdentifierAvailability{accounts: accounts}
}

// These public checks preserve the existing disclosure policy. get_user is a
// query-presence flag, and username fallback is allowed only after no email hit.
// No normalization, email syntax validation or active-account exclusion is added.
func (s *IdentifierAvailability) CheckEmail(ctx context.Context, value string, details bool) (domain.IdentifierAvailability, error) {
	empty := domain.IdentifierAvailability{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if utf8.RuneCountInString(value) > 255 {
		return empty, domain.ErrIdentifierTooLong
	}
	user, err := s.accounts.FirstMatchingIdentifier(ctx, true, value)
	if err != nil {
		return empty, err
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if user == nil && details {
		user, err = s.accounts.FirstMatchingIdentifier(ctx, false, value)
		if err != nil {
			return empty, err
		}
		if err := ctx.Err(); err != nil {
			return empty, err
		}
	}
	result := domain.IdentifierAvailability{Available: user == nil}
	if details {
		result.User = user
	}
	return result, nil
}
func (s *IdentifierAvailability) CheckUsername(ctx context.Context, value string) (domain.IdentifierAvailability, error) {
	empty := domain.IdentifierAvailability{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	if utf8.RuneCountInString(value) > 150 {
		return empty, domain.ErrIdentifierTooLong
	}
	exists, err := s.accounts.UsernameExists(ctx, value)
	if err != nil {
		return empty, err
	}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	return domain.IdentifierAvailability{Available: !exists}, nil
}
