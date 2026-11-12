package service

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type identifierFinder struct {
	calls           []string
	email, username *domain.IdentifierAccount
	failures        map[bool]error
	cancel          context.CancelFunc
}

func (f *identifierFinder) FirstMatchingIdentifier(ctx context.Context, email bool, value string) (*domain.IdentifierAccount, error) {
	prefix := "username:"
	result := f.username
	if email {
		prefix = "email:"
		result = f.email
	}
	f.calls = append(f.calls, prefix+value)
	if f.cancel != nil {
		f.cancel()
	}
	return result, f.failures[email]
}

func (f *identifierFinder) UsernameExists(ctx context.Context, value string) (bool, error) {
	f.calls = append(f.calls, "exists:"+value)
	if f.cancel != nil {
		f.cancel()
	}
	return f.username != nil, f.failures[false]
}

func TestIdentifierEmailSelectionAndDisclosure(t *testing.T) {
	email := &domain.IdentifierAccount{ID: "email-id", FirstName: "First", LastName: "Email", Email: "mail@example.test"}
	username := &domain.IdentifierAccount{ID: "username-id", FirstName: "Fallback", Email: "fallback@example.test"}
	for _, tc := range []struct {
		name        string
		email, user *domain.IdentifierAccount
		details     bool
		want        *domain.IdentifierAccount
		available   bool
		calls       []string
	}{
		{"unused without fallback", nil, username, false, nil, true, []string{"email:identifier"}},
		{"taken without disclosure", email, username, false, nil, false, []string{"email:identifier"}},
		{"email precedes username", email, username, true, email, false, []string{"email:identifier"}},
		{"username fallback", nil, username, true, username, false, []string{"email:identifier", "username:identifier"}},
		{"both missing", nil, nil, true, nil, true, []string{"email:identifier", "username:identifier"}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			finder := &identifierFinder{email: tc.email, username: tc.user}
			result, err := NewIdentifierAvailability(finder).CheckEmail(context.Background(), "identifier", tc.details)
			if err != nil || result.Available != tc.available || !reflect.DeepEqual(result.User, tc.want) || !reflect.DeepEqual(finder.calls, tc.calls) {
				t.Fatal("email selection, disclosure or bounded lookup differs", err)
			}
		})
	}
}

func TestIdentifierLengthsUseCharactersWithoutTrimming(t *testing.T) {
	for _, email := range []bool{true, false} {
		limit := 150
		if email {
			limit = 255
		}
		for _, char := range []string{"x", "è", "😀"} {
			for _, size := range []int{0, limit, limit + 1} {
				finder := &identifierFinder{}
				flow := NewIdentifierAvailability(finder)
				value := strings.Repeat(char, size)
				var err error
				if email {
					_, err = flow.CheckEmail(context.Background(), value, false)
				} else {
					_, err = flow.CheckUsername(context.Background(), value)
				}
				if size > limit {
					if !errors.Is(err, domain.ErrIdentifierTooLong) || len(finder.calls) != 0 {
						t.Fatal("oversized identifier reached repository")
					}
				} else if err != nil || len(finder.calls) != 1 || !strings.HasSuffix(finder.calls[0], ":"+value) {
					t.Fatal("character boundary differs", err)
				}
			}
		}
	}
	finder := &identifierFinder{}
	_, err := NewIdentifierAvailability(finder).CheckEmail(context.Background(), " literal%_ ", true)
	if err != nil || !reflect.DeepEqual(finder.calls, []string{"email: literal%_ ", "username: literal%_ "}) {
		t.Fatal("identifier was normalized")
	}
	finder = &identifierFinder{}
	_, err = NewIdentifierAvailability(finder).CheckEmail(context.Background(), strings.Repeat("x", 200), true)
	if err != nil || len(finder.calls) != 2 {
		t.Fatal("email fallback incorrectly imposed username length limit")
	}
}

func TestIdentifierUsernameNeverDisclosesAccount(t *testing.T) {
	finder := &identifierFinder{username: &domain.IdentifierAccount{ID: "private-id", Email: "private@example.test"}}
	result, err := NewIdentifierAvailability(finder).CheckUsername(context.Background(), "name")
	if err != nil || result.Available || result.User != nil || !reflect.DeepEqual(finder.calls, []string{"exists:name"}) {
		t.Fatal("username lookup leaked account or chose wrong predicate", err)
	}
}

func TestIdentifierFailuresDoNotBecomeAvailable(t *testing.T) {
	failure := errors.New("fixture database unavailable")
	for _, atEmail := range []bool{true, false} {
		finder := &identifierFinder{failures: map[bool]error{atEmail: failure}}
		_, err := NewIdentifierAvailability(finder).CheckEmail(context.Background(), "name", true)
		want := 2
		if atEmail {
			want = 1
		}
		if !errors.Is(err, failure) || len(finder.calls) != want {
			t.Fatal("repository failure became availability or fallback", err)
		}
	}
}

func TestIdentifierCancellationStopsLookupsAndDisclosure(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	finder := &identifierFinder{}
	if _, err := NewIdentifierAvailability(finder).CheckEmail(ctx, "name", true); !errors.Is(err, context.Canceled) || len(finder.calls) != 0 {
		t.Fatal("precancelled lookup reached database")
	}
	ctx, cancel = context.WithCancel(context.Background())
	defer cancel()
	finder = &identifierFinder{cancel: cancel}
	if _, err := NewIdentifierAvailability(finder).CheckEmail(ctx, "name", true); !errors.Is(err, context.Canceled) || len(finder.calls) != 1 {
		t.Fatal("cancelled lookup continued fallback")
	}
}
