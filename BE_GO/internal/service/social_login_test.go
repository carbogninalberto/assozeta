package service

import (
	"context"
	"errors"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type socialFixture struct {
	rows         []domain.Account
	email        any
	valid        bool
	err, readErr error
	calls        int
	closed       bool
	provider     string
	token        any
}

func (f *socialFixture) VisitLoginCandidates(ctx context.Context, email bool, identifier string, visit func(domain.Account) error) error {
	defer func() { f.closed = true }()
	for _, a := range f.rows {
		if err := visit(a); err != nil {
			return err
		}
	}
	return f.readErr
}
func (f *socialFixture) Verify(_ context.Context, provider string, token any) (SocialIdentity, bool, error) {
	if !f.closed {
		panic("provider called with database reader open")
	}
	f.calls++
	f.provider = provider
	f.token = token
	return SocialIdentity{Email: f.email}, f.valid, f.err
}
func TestSocialSelectionOrdering(t *testing.T) {
	account := domain.Account{ID: "one", Username: "TARGET", Email: "target@example.test", Password: "must-not-escape", Active: true}
	for _, tc := range []struct {
		name     string
		rows     []domain.Account
		backend  any
		email    any
		valid    bool
		want     error
		selected string
		calls    int
	}{
		{"singleton", []domain.Account{account}, "google", "TARGET@EXAMPLE.TEST", true, nil, "one", 1},
		{"missing before backend", nil, nil, nil, false, ErrLoginNotFound, "", 0},
		{"ambiguous before backend", []domain.Account{{ID: "a", Username: "a"}, {ID: "b", Username: "b"}}, nil, nil, false, ErrLoginCredentials, "", 0},
		{"preferred wins", []domain.Account{{ID: "a", Username: "a"}, account}, "apple", account.Email, true, nil, "one", 1},
		{"invalid token", []domain.Account{account}, "google", nil, false, ErrSocialToken, "", 1},
		{"wrong email", []domain.Account{account}, "google", "else@example.test", true, ErrSocialEmail, "", 1},
		{"missing email", []domain.Account{account}, "google", nil, true, ErrSocialIdentity, "", 1},
		{"nontext email", []domain.Account{account}, "google", true, true, ErrSocialIdentity, "", 1},
		{"inactive after token", []domain.Account{{ID: "one", Username: "TARGET", Email: account.Email}}, "google", nil, false, ErrSocialToken, "", 1},
		{"inactive after email", []domain.Account{{ID: "one", Username: "TARGET", Email: account.Email}}, "google", account.Email, true, ErrLoginCredentials, "", 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := &socialFixture{rows: tc.rows, email: tc.email, valid: tc.valid}
			got, err := NewSocialLogin(f, f, 2).Select(context.Background(), " target ", []any{"opaque"}, tc.backend)
			if !errors.Is(err, tc.want) || f.calls != tc.calls {
				t.Fatal("wrong error or provider order", err, f.calls)
			}
			if tc.selected != "" {
				if got == nil || got.ID != tc.selected || got.Password != "" {
					t.Fatal("selection or hash disclosure")
				}
			} else if got != nil {
				t.Fatal("failed credentials admitted")
			}
		})
	}
	for _, backend := range []string{"google", "google-oauth2", "google-identity", "apple", "apple-id"} {
		f := &socialFixture{rows: []domain.Account{account}, email: account.Email, valid: true}
		token := domain.RequestObject{{Name: "raw", Value: nil}}
		if _, err := NewSocialLogin(f, f, 1).Select(context.Background(), "target", token, backend); err != nil || !reflect.DeepEqual(token, f.token) {
			t.Fatal("provider input changed")
		}
		want := "google"
		if strings.HasPrefix(backend, "apple") {
			want = "apple"
		}
		if f.provider != want {
			t.Fatal("wrong provider")
		}
	}
}
func TestSocialFailuresAndCancellation(t *testing.T) {
	account := domain.Account{ID: "one", Username: "TARGET", Email: "target@example.test", Active: true}
	for _, backend := range []any{nil, true, []any{"google"}, "GOOGLE"} {
		f := &socialFixture{rows: []domain.Account{account}}
		got, err := NewSocialLogin(f, f, 1).Select(context.Background(), "target", nil, backend)
		var unsupported *UnsupportedSocialBackend
		if got != nil || !errors.As(err, &unsupported) || f.calls != 0 {
			t.Fatal("unsupported backend ordering")
		}
	}
	failure := errors.New("private error")
	for _, read := range []bool{false, true} {
		f := &socialFixture{rows: []domain.Account{account}, valid: true, email: account.Email}
		if read {
			f.readErr = failure
		} else {
			f.err = failure
		}
		got, err := NewSocialLogin(f, f, 1).Select(context.Background(), "target", "opaque", "google")
		if got != nil || !errors.Is(err, failure) {
			t.Fatal("dependency error swallowed")
		}
	}
	f := &socialFixture{}
	s := NewSocialLogin(f, f, 1)
	s.slots <- struct{}{}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if got, err := s.Select(ctx, "target", "opaque", "google"); got != nil || !errors.Is(err, context.Canceled) || f.closed || f.calls != 0 {
		t.Fatal("cancelled request acquired dependencies")
	}
	<-s.slots
}
func TestSocialEmailLowercaseIsNotCasefold(t *testing.T) {
	for _, tc := range []struct {
		stored, provider string
		ok               bool
	}{
		{"straße@example.test", "STRASSE@example.test", false},
		{"ΟΣ@example.test", "ος@example.test", true},
		{"İ@example.test", "i\u0307@example.test", true},
	} {
		f := &socialFixture{rows: []domain.Account{{ID: "one", Username: "TARGET", Email: tc.stored, Active: true}}, email: tc.provider, valid: true}
		got, err := NewSocialLogin(f, f, 1).Select(context.Background(), "target", nil, "google")
		if (err == nil) != tc.ok || (got != nil) != tc.ok {
			t.Fatal("email lowercase semantics differ")
		}
	}
}

type parallelSocialAccounts struct{ reads atomic.Int64 }

func (s *parallelSocialAccounts) VisitLoginCandidates(ctx context.Context, _ bool, _ string, visit func(domain.Account) error) error {
	s.reads.Add(1)
	return visit(domain.Account{ID: "one", Username: "TARGET", Email: "target@example.test", Active: true})
}

type blockingSocialVerifier struct {
	started                chan struct{}
	release                chan struct{}
	active, maximum, calls atomic.Int64
}

func (v *blockingSocialVerifier) Verify(ctx context.Context, _ string, _ any) (SocialIdentity, bool, error) {
	n := v.active.Add(1)
	defer v.active.Add(-1)
	v.calls.Add(1)
	for {
		old := v.maximum.Load()
		if n <= old || v.maximum.CompareAndSwap(old, n) {
			break
		}
	}
	v.started <- struct{}{}
	select {
	case <-ctx.Done():
		return SocialIdentity{}, false, ctx.Err()
	case <-v.release:
		return SocialIdentity{Email: "target@example.test"}, true, nil
	}
}
func TestSocialAdmissionBoundAndCancelledWaiter(t *testing.T) {
	accounts := &parallelSocialAccounts{}
	provider := &blockingSocialVerifier{started: make(chan struct{}, 4), release: make(chan struct{})}
	defer close(provider.release)
	flow := NewSocialLogin(accounts, provider, 2)
	first, cancelFirst := context.WithCancel(context.Background())
	defer cancelFirst()
	second, cancelSecond := context.WithCancel(context.Background())
	defer cancelSecond()
	results := make(chan error, 4)
	launch := func(ctx context.Context) {
		go func() {
			got, err := flow.Select(ctx, "target", "opaque", "google")
			if err != nil && got != nil {
				results <- errors.New("cancelled provider admitted")
				return
			}
			results <- err
		}()
	}
	awaitStart := func() {
		select {
		case <-provider.started:
		case <-time.After(2 * time.Second):
			t.Fatal("provider not entered")
		}
	}
	launch(first)
	launch(second)
	awaitStart()
	awaitStart()
	waiting, cancelWaiting := context.WithCancel(context.Background())
	launch(waiting)
	cancelWaiting()
	if err := <-results; !errors.Is(err, context.Canceled) {
		t.Fatal("waiting request did not cancel")
	}
	if provider.calls.Load() != 2 || accounts.reads.Load() != 2 {
		t.Fatal("waiting request acquired a dependency")
	}
	cancelFirst()
	if err := <-results; !errors.Is(err, context.Canceled) {
		t.Fatal("running provider did not cancel")
	}
	retry, cancelRetry := context.WithCancel(context.Background())
	defer cancelRetry()
	launch(retry)
	awaitStart()
	cancelSecond()
	cancelRetry()
	for i := 0; i < 2; i++ {
		if err := <-results; !errors.Is(err, context.Canceled) {
			t.Fatal("provider cancellation")
		}
	}
	if provider.maximum.Load() != 2 || provider.calls.Load() != 3 || accounts.reads.Load() != 3 || len(flow.slots) != 0 {
		t.Fatal("admission limit or reuse")
	}
}
