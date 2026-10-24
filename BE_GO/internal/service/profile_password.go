package service

import (
	"context"
	"errors"
	"strings"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	passwordcrypto "github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto"
)

type ProfilePasswordOutcome int

const (
	ProfilePasswordIncorrect ProfilePasswordOutcome = iota
	ProfilePasswordInvalid
	ProfilePasswordChanged
)

type ProfilePasswordHasher interface {
	CheckDjangoPassword(context.Context, passwordcrypto.LoginPasswordValue, string) (passwordcrypto.LoginPasswordDecision, error)
	Encode(context.Context, string) (string, error)
}
type ProfilePasswords struct {
	store  domain.ProfilePasswordStore
	hashes ProfilePasswordHasher
	slots  chan struct{}
}

func NewProfilePasswords(store domain.ProfilePasswordStore, hashes ProfilePasswordHasher, concurrency int) *ProfilePasswords {
	if concurrency < 1 {
		panic("profile password concurrency must be positive")
	}
	return &ProfilePasswords{store, hashes, make(chan struct{}, concurrency)}
}
func validProfilePassword(value string) bool {
	// Python's non-DOTALL . and $ permit one final LF, which remains in the
	// stored password. Length is Unicode code points; upper/digit tests are ASCII.
	text := strings.TrimSuffix(value, "\n")
	if strings.Contains(text, "\n") || utf8.RuneCountInString(text) < 10 {
		return false
	}
	upper, digit, special := false, false, false
	for _, r := range text {
		upper = upper || (r >= 'A' && r <= 'Z')
		digit = digit || (r >= '0' && r <= '9')
		special = special || strings.ContainsRune("!@#$&.-_*", r)
	}
	return upper && digit && special
}
func (s *ProfilePasswords) Change(ctx context.Context, identity domain.ResolvedIdentity, input dto.ProfilePasswordRequest) (ProfilePasswordOutcome, error) {
	if err := ctx.Err(); err != nil {
		return ProfilePasswordIncorrect, err
	}
	if identity.Actor == nil || identity.Effective == nil || identity.ScopeUser == nil || identity.Effective.ID == "" || identity.ImpersonationAssociationID != "" {
		return ProfilePasswordIncorrect, domain.ErrInvalidScopedRequest
	}
	select {
	case s.slots <- struct{}{}:
	case <-ctx.Done():
		return ProfilePasswordIncorrect, ctx.Err()
	}
	defer func() { <-s.slots }()
	text, err := loginValueText(ctx, input.Current)
	if err != nil {
		return ProfilePasswordIncorrect, err
	}
	_, isString := input.Current.(string)
	value := passwordcrypto.LoginPasswordValue{Text: text, String: isString, Null: input.Current == nil}
	for attempt := 0; attempt < 3; attempt++ {
		outcome, err := s.changeAttempt(ctx, identity.Effective.ID, input.New, value)
		if !errors.Is(err, domain.ErrCredentialConflict) {
			return outcome, err
		}
	}
	return ProfilePasswordIncorrect, domain.ErrCredentialConflict
}
func (s *ProfilePasswords) changeAttempt(ctx context.Context, id, next string, current passwordcrypto.LoginPasswordValue) (ProfilePasswordOutcome, error) {
	tx, err := s.store.BeginProfilePassword(ctx)
	if err != nil {
		return ProfilePasswordIncorrect, err
	}
	defer tx.Rollback()
	encoded, err := tx.PasswordForUpdate(ctx, id)
	if err != nil {
		return ProfilePasswordIncorrect, err
	}
	checked, err := s.hashes.CheckDjangoPassword(ctx, current, encoded)
	if err != nil {
		return ProfilePasswordIncorrect, err
	}
	outcome := ProfilePasswordIncorrect
	if !validProfilePassword(next) {
		outcome = ProfilePasswordInvalid
	} else if checked.Match {
		outcome = ProfilePasswordChanged
	}
	if checked.Upgrade || outcome == ProfilePasswordChanged {
		replacement := current.Text
		if outcome == ProfilePasswordChanged {
			replacement = next
		}
		encoded, err = s.hashes.Encode(ctx, replacement)
		if err != nil {
			return ProfilePasswordIncorrect, err
		}
		if err = tx.SetPassword(ctx, id, encoded); err != nil {
			return ProfilePasswordIncorrect, err
		}
	}
	if err = ctx.Err(); err != nil {
		return ProfilePasswordIncorrect, err
	}
	if err = tx.Commit(); err != nil {
		return ProfilePasswordIncorrect, err
	}
	return outcome, nil
}

type ProfilePasswordResponses struct {
	profiles     LoginResponseProfiles
	associations LoginResponseAssociations
	images       domain.LoginImageProcessor
}

func NewProfilePasswordResponses(profiles LoginResponseProfiles, associations LoginResponseAssociations, images domain.LoginImageProcessor) *ProfilePasswordResponses {
	return &ProfilePasswordResponses{profiles, associations, images}
}
func (s *ProfilePasswordResponses) Read(ctx context.Context, identity domain.ResolvedIdentity, outcome ProfilePasswordOutcome) (*dto.ProfilePasswordResponse, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if identity.Effective == nil || identity.ScopeUser == nil {
		return nil, domain.ErrInvalidScopedRequest
	}
	user := identity.Effective
	role := "association"
	switch user.Role {
	case -2, 0, 1, 3:
	case -1, 2:
		role = "athlete"
	default:
		return nil, ErrLoginResponseRole
	}
	profile, err := s.profiles.Read(ctx, user.ID)
	if err != nil {
		return nil, err
	}
	if profile == nil || profile.UserID != user.ID {
		return nil, domain.ErrAccountNotFound
	}
	result := &dto.ProfilePasswordResponse{Message: "old password not correct.", Code: "EC000", Role: role, UserData: dto.ProfilePasswordUser{UserAuthProfile: *profile}}
	if outcome == ProfilePasswordChanged {
		result.Message = "password updated with success."
		result.Code = ""
	}
	compress := func(value *string) (*string, error) {
		if value == nil {
			return nil, nil
		}
		if s.images == nil {
			return nil, ErrLoginImageProcessorUnavailable
		}
		out, err := s.images.CompressLoginImage(ctx, *value)
		if err != nil {
			return nil, err
		}
		return &out, nil
	}
	result.UserData.AvatarImage, err = compress(profile.AvatarImage)
	if err != nil {
		return nil, err
	}
	if user.Role == domain.RoleAssociation {
		if identity.ScopeUser.ID != user.ID {
			return nil, domain.ErrInvalidScopedRequest
		}
		association, err := s.associations.Read(ctx, user.ID)
		if err != nil {
			return nil, err
		}
		if association == nil || association.ID == "" {
			return nil, domain.ErrAssociationNotFound
		}
		copy := *association
		copy.Logo, err = compress(association.Logo)
		if err != nil {
			return nil, err
		}
		result.UserData.SportAssociation = &copy
	}
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	return result, nil
}

var ErrProfilePasswordInvalid = errors.New("new profile password does not satisfy policy")

type ProfilePasswordUpdates struct {
	passwords *ProfilePasswords
	responses *ProfilePasswordResponses
}

func NewProfilePasswordUpdates(passwords *ProfilePasswords, responses *ProfilePasswordResponses) *ProfilePasswordUpdates {
	return &ProfilePasswordUpdates{passwords, responses}
}
func (s *ProfilePasswordUpdates) Update(ctx context.Context, identity domain.ResolvedIdentity, input dto.ProfilePasswordRequest) (*dto.ProfilePasswordResponse, error) {
	outcome, err := s.passwords.Change(ctx, identity, input)
	if err != nil {
		return nil, err
	}
	if outcome == ProfilePasswordInvalid {
		return nil, ErrProfilePasswordInvalid
	}
	// Django commits credential changes before serializing profile/images. A
	// later response failure does not undo an already changed password.
	return s.responses.Read(ctx, identity, outcome)
}
