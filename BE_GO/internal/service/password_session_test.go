package service

import (
	"context"
	"errors"
	"reflect"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
)

type sessionStages struct {
	t              *testing.T
	calls          []string
	fail, cancelAt string
	cancel         context.CancelFunc
	account        *domain.Account
	audit          domain.LoginAuditContext
	pair           jwt.Pair
	response       *dto.LoginResponse
}

var sessionFailure = errors.New("synthetic session stage failed")

func (s *sessionStages) stage(name string) error {
	s.calls = append(s.calls, name)
	if name == s.cancelAt {
		s.cancel()
	}
	if name == s.fail {
		return sessionFailure
	}
	return nil
}
func (s *sessionStages) Admit(_ context.Context, identifier, password string, otp any, present bool, audit domain.LoginAuditContext) (*domain.Account, error) {
	if identifier != "identifier" || password != "password" || otp != "287082" || !present || !reflect.DeepEqual(audit, s.audit) {
		s.t.Fatal("admission input changed")
	}
	return s.account, s.stage("admit")
}
func (s *sessionStages) Issue(id string, role int, email string) (jwt.Pair, error) {
	if id != s.account.ID || role != s.account.Role || email != s.account.Email {
		s.t.Fatal("token identity differs from admitted account")
	}
	return s.pair, s.stage("issue")
}
func (s *sessionStages) Build(_ context.Context, account domain.Account, pair jwt.Pair, audit domain.LoginAuditContext) (*dto.LoginResponse, error) {
	if account.ID != s.account.ID || pair != s.pair || !reflect.DeepEqual(audit, s.audit) {
		s.t.Fatal("response identity or audit changed")
	}
	return s.response, s.stage("build")
}
func (s *sessionStages) Finalize(_ context.Context, id string, audit domain.LoginAuditContext) error {
	if id != s.account.ID || !reflect.DeepEqual(audit, s.audit) {
		s.t.Fatal("finalization identity or audit changed")
	}
	return s.stage("finalize")
}
func sessionFixture(t *testing.T) *sessionStages {
	actor := "independent-actor"
	pair := jwt.Pair{AccessToken: "synthetic-access", RefreshToken: "synthetic-refresh", ExpiresIn: 14400, TokenType: "Bearer"}
	return &sessionStages{t: t, account: &domain.Account{ID: "target", Email: "target@example.test", Role: 2, Active: true}, audit: domain.LoginAuditContext{ActorID: &actor}, pair: pair, response: &dto.LoginResponse{Pair: pair, UserData: dto.LoginUserProfile{UserAuthProfile: dto.UserAuthProfile{UserID: "target"}}}}
}
func TestPasswordSessionOrderAndNoDeliveryBeforeFinalization(t *testing.T) {
	stages := []string{"admit", "issue", "build", "finalize"}
	for _, mode := range []string{"success", "failure", "cancellation"} {
		for i, stage := range stages {
			t.Run(mode+"/"+stage, func(t *testing.T) {
				s := sessionFixture(t)
				ctx, cancel := context.WithCancel(context.Background())
				defer cancel()
				s.cancel = cancel
				if mode == "failure" {
					s.fail = stage
				}
				if mode == "cancellation" {
					s.cancelAt = stage
				}
				flow := NewPasswordSessions(s, s, s, s)
				got, err := flow.Login(ctx, "identifier", "password", "287082", true, s.audit)
				want := stages
				if mode != "success" {
					want = stages[:i+1]
					if got != nil {
						t.Fatal("response exposed on failed or canceled stage")
					}
					if mode == "failure" && !errors.Is(err, sessionFailure) {
						t.Fatal("stage error lost", err)
					}
					if mode == "cancellation" && !errors.Is(err, context.Canceled) {
						t.Fatal("cancellation lost", err)
					}
				} else if err != nil || got != s.response {
					t.Fatal("successful response missing", err)
				}
				if !reflect.DeepEqual(s.calls, want) {
					t.Fatal("wrong execution order", s.calls, want)
				}
			})
		}
	}
}
func TestPasswordSessionRejectsBrokenDependencyResults(t *testing.T) {
	for _, kind := range []string{"nil account", "empty account", "nil response", "foreign response", "foreign tokens", "canceled entry"} {
		t.Run(kind, func(t *testing.T) {
			s := sessionFixture(t)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			want := []string{"admit"}
			switch kind {
			case "nil account":
				s.account = nil
			case "empty account":
				s.account.ID = ""
			case "nil response":
				s.response = nil
				want = []string{"admit", "issue", "build"}
			case "foreign response":
				s.response.UserData.UserID = "foreign"
				want = []string{"admit", "issue", "build"}
			case "foreign tokens":
				s.response.AccessToken = "foreign"
				want = []string{"admit", "issue", "build"}
			case "canceled entry":
				cancel()
				want = nil
			}
			got, err := NewPasswordSessions(s, s, s, s).Login(ctx, "identifier", "password", "287082", true, s.audit)
			if got != nil || err == nil || !reflect.DeepEqual(s.calls, want) {
				t.Fatal("invalid dependency result admitted", s.calls, err)
			}
		})
	}
}
