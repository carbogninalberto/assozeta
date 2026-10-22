//go:build sessionintegration

package service

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	passwordcrypto "github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto"
	"github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

type sessionQueryCounter struct{ count atomic.Int64 }

func (q *sessionQueryCounter) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	sql := strings.ToLower(strings.TrimSpace(d.SQL))
	if strings.HasPrefix(sql, "select") || strings.HasPrefix(sql, "insert") || strings.HasPrefix(sql, "update") {
		q.count.Add(1)
	}
	return ctx
}
func (*sessionQueryCounter) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}
func TestPasswordSessionPostgres(t *testing.T) {
	data, err := os.ReadFile(os.Getenv("ASSOZETA_PASSWORD_SESSION_FIXTURE"))
	if err != nil {
		t.Fatal("run accounts adapter", err)
	}
	var fixture struct {
		Now        string
		PrivateKey string `json:"private_key"`
		PublicKey  string `json:"public_key"`
		Cases      []struct {
			Name, ID, Identifier, Password, Outcome string
			Mode, Backend, Token                    string
			SocialValid                             bool `json:"social_valid"`
			OTP                                     json.RawMessage
			Present                                 bool
			Queries                                 int
			Expected                                json.RawMessage
		}
	}
	if err = json.Unmarshal(data, &fixture); err != nil || len(fixture.Cases) != 26 {
		t.Fatal("invalid session fixture", err)
	}
	now, err := time.Parse(time.RFC3339Nano, fixture.Now)
	if err != nil {
		t.Fatal(err)
	}
	clock := func() time.Time { return now }
	manager, err := jwt.New([]byte(fixture.PrivateKey), []byte(fixture.PublicKey), clock)
	if err != nil {
		t.Fatal("invalid fixture signing keys")
	}
	cfg, err := pgx.ParseConfig(os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal("private database config invalid")
	}
	cfg.DefaultQueryExecMode = pgx.QueryExecModeExec // Same mode as cmd/server and transaction poolers.
	counter := &sessionQueryCounter{}
	cfg.Tracer = counter
	db := sqlx.NewDb(stdlib.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	admission := NewPasswordAdmission(NewPasswordLogin(repository.NewPasswordLogins(db), passwordcrypto.NewPasswordVerifier(passwordcrypto.DefaultPasswordLimits()), 2), NewLoginPreparation(repository.NewLoginPreparations(db), clock), NewSecondFactor(func() time.Time { return time.Unix(59, 0) }))
	responses := NewLoginResponses(NewLoginProfiles(repository.NewLoginProfiles(db), clock), NewAssociationProfiles(repository.NewAssociationProfiles(db), clock), repository.NewLoginResponseSettings(db), NewMembershipCardConfigurations(repository.NewMembershipCardConfigurations(db), clock), nil, domain.RegistrationChoices{Family: true})
	socialTokens := &sessionSocialVerifier{db: db, values: map[string]sessionSocialIdentity{}}
	for _, tc := range fixture.Cases {
		if tc.Mode == "social" {
			provider := "google"
			if strings.HasPrefix(tc.Backend, "apple") {
				provider = "apple"
			}
			socialTokens.values[tc.Token] = sessionSocialIdentity{email: tc.Identifier, valid: tc.SocialValid, provider: provider}
		}
	}
	social := NewSocialAdmission(NewSocialLogin(repository.NewAccounts(db), socialTokens, 2), NewLoginPreparation(repository.NewLoginPreparations(db), clock), NewSecondFactor(func() time.Time { return time.Unix(59, 0) }))
	flow := NewLoginSessions(admission, social, manager, responses, NewLoginFinalization(repository.NewLoginFinalizations(db), clock))
	remote := "127.0.0.1"
	audit := domain.LoginAuditContext{RemoteAddress: &remote}
	results := make([]*dto.LoginResponse, len(fixture.Cases))
	for i, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			otp, err := dto.ParseReferenceJSON(tc.OTP)
			if err != nil {
				t.Fatal("invalid OTP fixture")
			}
			counter.count.Store(0)
			socialTokens.calls.Store(0)
			result, err := flow.LoginRequest(context.Background(), dto.LoginRequest{Mode: tc.Mode, Identifier: tc.Identifier, Password: tc.Password, Token: tc.Token, Backend: tc.Backend, OTP: otp, OTPPresent: tc.Present}, audit)
			outcome := ""
			var databaseError *pgconn.PgError
			switch {
			case err == nil:
			case errors.Is(err, ErrSocialToken):
				outcome = "social_token"
			case errors.Is(err, ErrOTPRequired):
				outcome = "required"
			case errors.Is(err, ErrOTPInvalid):
				outcome = "invalid_otp"
			case errors.Is(err, ErrLoginCredentials):
				outcome = "invalid_credentials"
			case errors.Is(err, ErrLoginNotFound):
				outcome = "not_found"
			case errors.Is(err, ErrLoginResponseRole):
				outcome = "role"
			case errors.Is(err, domain.ErrAssociationNotFound):
				outcome = "association"
			case errors.As(err, &databaseError) && databaseError.Code == "23514":
				outcome = "finalization"
			default:
				t.Fatal("unexpected session failure", err)
			}
			wantCalls := int64(0)
			if tc.Mode == "social" {
				wantCalls = 1
			}
			if socialTokens.calls.Load() != wantCalls {
				t.Fatal("wrong social session dispatch")
			}
			if outcome != tc.Outcome {
				t.Fatal("wrong session outcome", outcome, tc.Outcome)
			}
			if got := counter.count.Load(); got != int64(tc.Queries) {
				t.Errorf("query count %d want %d", got, tc.Queries)
			}
			if outcome != "" {
				if result != nil {
					t.Fatal("failed session exposed tokens")
				}
				return
			}
			if result == nil {
				t.Fatal("missing session response")
			}
			for kind, token := range map[string]string{"access": result.AccessToken, "refresh": result.RefreshToken} {
				claims, err := manager.Verify(token, kind)
				if err != nil || claims["user_id"] != tc.ID {
					t.Fatal("wrong signed identity")
				}
			}
			encoded, err := json.Marshal(result)
			if err != nil {
				t.Fatal(err)
			}
			var actual, expected map[string]any
			decode := func(data []byte, target any) error {
				d := json.NewDecoder(bytes.NewReader(data))
				d.UseNumber()
				return d.Decode(target)
			}
			if decode(encoded, &actual) != nil || decode(tc.Expected, &expected) != nil {
				t.Fatal("invalid response JSON")
			}
			delete(actual, "access_token")
			delete(actual, "refresh_token")
			if !reflect.DeepEqual(actual, expected) {
				t.Fatal("declared full response differs")
			}
			results[i] = result
		})
	}
	// Repeated concurrent logins reuse one coordinator and its bounded password
	// stage. Existing username/card invariants must not duplicate on these retries.
	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func(i int) {
			defer wg.Done()
			tc := fixture.Cases[i%2]
			result, err := flow.LoginRequest(context.Background(), dto.LoginRequest{Mode: "password", Identifier: tc.Identifier, Password: tc.Password}, audit)
			if err != nil || result == nil || result.UserData.UserID != tc.ID {
				t.Error("concurrent session failed", err)
			}
		}(i)
	}
	wg.Wait()
	if db.Stats().InUse != 0 {
		t.Fatal("session retained database connection")
	}
	encoded, err := json.Marshal(results)
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(os.Getenv("ASSOZETA_PASSWORD_SESSION_RESULT"), encoded, 0600); err != nil {
		t.Fatal(err)
	}
}

type sessionSocialIdentity struct {
	email, provider string
	valid           bool
}
type sessionSocialVerifier struct {
	db     *sqlx.DB
	values map[string]sessionSocialIdentity
	calls  atomic.Int64
}

func (v *sessionSocialVerifier) Verify(ctx context.Context, provider string, token any) (SocialIdentity, bool, error) {
	v.calls.Add(1)
	key, ok := token.(string)
	if !ok {
		return SocialIdentity{}, false, errors.New("invalid fixture token type")
	}
	value, ok := v.values[key]
	if !ok || provider != value.provider || v.db.Stats().InUse != 0 {
		return SocialIdentity{}, false, errors.New("social dispatch or database lifetime differs")
	}
	return SocialIdentity{Email: value.email}, value.valid, ctx.Err()
}
