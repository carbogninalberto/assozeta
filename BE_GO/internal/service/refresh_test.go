package service

import (
	"context"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
)

type refreshTokenStub struct {
	calls, unbound int
	err            error
	cancel         context.CancelFunc
}

func (s *refreshTokenStub) Refresh(string) (jwt.Pair, error) {
	s.calls++
	if s.cancel != nil {
		s.cancel()
	}
	return jwt.Pair{AccessToken: "synthetic"}, s.err
}
func (s *refreshTokenStub) IssueUnbound() (jwt.Pair, error) {
	s.unbound++
	return jwt.Pair{AccessToken: "synthetic"}, s.err
}
func TestRefreshServiceAdmissionAndFailure(t *testing.T) {
	tokens := &refreshTokenStub{}
	s := NewRefreshService(tokens)
	if _, err := s.Refresh(context.Background(), nil, false); !errors.Is(err, ErrRefreshRequired) {
		t.Fatal("missing field admitted")
	}
	for _, value := range []any{false, 0, []any{}, map[string]any{}} {
		if _, err := s.Refresh(context.Background(), value, true); !errors.Is(err, ErrRefreshInvalid) {
			t.Fatal("non-token value admitted")
		}
	}
	if tokens.calls != 0 || tokens.unbound != 0 {
		t.Fatal("invalid input reached signer")
	}
	if _, err := s.Refresh(context.Background(), nil, true); err != nil || tokens.unbound != 1 {
		t.Fatal("explicit null constructor behavior changed")
	}
	tokens.err = errors.New("signer unavailable")
	if pair, err := s.Refresh(context.Background(), "token", true); !errors.Is(err, ErrRefreshInvalid) || pair.AccessToken != "" {
		t.Fatal("signer failure returned credential")
	}
	ctx, cancel := context.WithCancel(context.Background())
	tokens.err = nil
	tokens.cancel = cancel
	if pair, err := s.Refresh(ctx, "token", true); !errors.Is(err, context.Canceled) || pair.AccessToken != "" {
		t.Fatal("cancelled signing returned credential")
	}
}
