//go:build socialintegration

package service

import (
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

	ownedpgx "github.com/carbogninalberto/assozeta/BE_GO/internal/database"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"github.com/jackc/pgx/v5"
	"github.com/jmoiron/sqlx"
)

type socialQueryCounter struct{ count atomic.Int64 }

func (q *socialQueryCounter) TraceQueryStart(ctx context.Context, _ *pgx.Conn, d pgx.TraceQueryStartData) context.Context {
	sql := strings.ToLower(strings.TrimSpace(d.SQL))
	if strings.HasPrefix(sql, "select") || strings.HasPrefix(sql, "insert") || strings.HasPrefix(sql, "update") {
		q.count.Add(1)
	}
	return ctx
}
func (*socialQueryCounter) TraceQueryEnd(context.Context, *pgx.Conn, pgx.TraceQueryEndData) {}

type controlledSocialVerifier struct {
	db           *sqlx.DB
	token, email any
	provider     string
	valid, fail  bool
	calls        int
}

var socialProviderFailure = errors.New("synthetic provider failure")

func (v *controlledSocialVerifier) Verify(ctx context.Context, provider string, token any) (SocialIdentity, bool, error) {
	if v.db.Stats().InUse != 0 {
		return SocialIdentity{}, false, errors.New("provider called with database connection retained")
	}
	if !reflect.DeepEqual(token, v.token) || provider != v.provider {
		return SocialIdentity{}, false, errors.New("provider input changed")
	}
	v.calls++
	if v.fail {
		return SocialIdentity{}, false, socialProviderFailure
	}
	return SocialIdentity{Email: v.email}, v.valid, nil
}

type batchSocialVerifier struct{ calls atomic.Int64 }

func (v *batchSocialVerifier) Verify(ctx context.Context, provider string, token any) (SocialIdentity, bool, error) {
	v.calls.Add(1)
	return SocialIdentity{Email: token}, true, ctx.Err()
}

func TestSocialPostgres(t *testing.T) {
	raw, err := os.ReadFile(os.Getenv("ASSOZETA_SOCIAL_FIXTURE"))
	if err != nil {
		t.Fatal("run accounts adapter")
	}
	var fixture struct {
		Cases []struct {
			Name, Selected, Outcome, Provider      string
			BackendText                            string `json:"backend_text"`
			Identifier, Token, Backend, Email, OTP json.RawMessage
			Valid, Present                         bool
			ProviderError                          bool `json:"provider_error"`
			ProviderCalls                          int  `json:"provider_calls"`
			Actor                                  *string
			Queries                                int
		}
		Batches []struct{ Identifier, Selected string }
	}
	// The case count is an explicit gate, not silently inferred from discovery.
	if json.Unmarshal(raw, &fixture) != nil || len(fixture.Cases) != 41 || len(fixture.Batches) != 4 {
		t.Fatal("invalid social fixture")
	}
	cfg, err := pgx.ParseConfig(os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal("private database configuration")
	}
	counter := &socialQueryCounter{}
	cfg.Tracer = counter
	db := sqlx.NewDb(ownedpgx.OpenDB(*cfg), "pgx")
	defer db.Close()
	db.SetMaxOpenConns(4)
	parse := func(raw json.RawMessage) any {
		value, err := dto.ParseReferenceJSON(raw)
		if err != nil {
			t.Fatal("invalid request fixture")
		}
		return value
	}
	accounts := repository.NewAccounts(db)
	for _, tc := range fixture.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			verifier := &controlledSocialVerifier{db: db, token: parse(tc.Token), email: parse(tc.Email), provider: tc.Provider, valid: tc.Valid, fail: tc.ProviderError}
			flow := NewSocialAdmission(NewSocialLogin(accounts, verifier, 2), NewLoginPreparation(repository.NewLoginPreparations(db), time.Now), NewSecondFactor(func() time.Time { return time.Unix(59, 0) }))
			remote, actorEmail := "127.0.0.1", "social-audit-actor@example.test"
			audit := domain.LoginAuditContext{ActorID: tc.Actor, RemoteAddress: &remote}
			if tc.Actor != nil {
				audit.ActorEmail = &actorEmail
			}
			counter.count.Store(0)
			account, err := flow.Admit(context.Background(), parse(tc.Identifier), parse(tc.Token), parse(tc.Backend), parse(tc.OTP), tc.Present, audit)
			outcome := ""
			var unsupported *UnsupportedSocialBackend
			switch {
			case err == nil:
			case errors.Is(err, ErrLoginNotFound):
				outcome = "not_found"
			case errors.Is(err, ErrLoginCredentials):
				outcome = "invalid"
			case errors.Is(err, ErrSocialToken):
				outcome = "token"
			case errors.Is(err, ErrSocialEmail):
				outcome = "email"
			case errors.Is(err, ErrSocialIdentity):
				outcome = "identity"
			case errors.As(err, &unsupported):
				outcome = "backend"
				if unsupported.Value != tc.BackendText {
					t.Fatal("unsupported backend representation changed")
				}
			case errors.Is(err, socialProviderFailure):
				outcome = "provider"
			case errors.Is(err, ErrOTPRequired):
				outcome = "required"
			case errors.Is(err, ErrOTPInvalid):
				outcome = "invalid_otp"
			case errors.Is(err, ErrOTPShape):
				outcome = "otp_type"
			default:
				t.Fatal("unexpected social admission error", err)
			}
			if outcome != tc.Outcome || verifier.calls != tc.ProviderCalls {
				t.Fatal("social outcome/provider calls", outcome, tc.Outcome, verifier.calls, tc.ProviderCalls)
			}
			if outcome == "" {
				if account == nil || account.ID != tc.Selected || account.Password != "" {
					t.Fatal("wrong admitted identity")
				}
			} else if account != nil {
				t.Fatal("failed admission exposed account")
			}
			// Selection alone is nontransactional. Admission reaches the owned
			// LoginPreparation transaction before successful or rejected OTP, so
			// those outcomes include one acknowledged server fence SELECT.
			protocol := 0
			switch tc.Outcome {
			case "", "required", "invalid_otp", "otp_type":
				protocol = 1
			}
			if counter.count.Load() != int64(tc.Queries+protocol) {
				t.Fatal("query count including admission fence", counter.count.Load(), tc.Queries+protocol)
			}
			if db.Stats().InUse != 0 {
				t.Fatal("connection retained")
			}
		})
	}
	verifier := &batchSocialVerifier{}
	selection := NewSocialLogin(accounts, verifier, 2)
	for _, batch := range fixture.Batches {
		counter.count.Store(0)
		got, err := selection.Select(context.Background(), batch.Identifier, batch.Identifier, "google")
		if err != nil || got == nil || got.ID != batch.Selected || counter.count.Load() != 1 {
			t.Fatal("streamed selection/query bound")
		}
	}
	counter.count.Store(0)
	var group sync.WaitGroup
	for i := 0; i < 32; i++ {
		group.Add(1)
		go func(i int) {
			defer group.Done()
			batch := fixture.Batches[i%4]
			got, err := selection.Select(context.Background(), batch.Identifier, batch.Identifier, "apple")
			if err != nil || got == nil || got.ID != batch.Selected {
				t.Error("concurrent social selection failed")
			}
		}(i)
	}
	group.Wait()
	if counter.count.Load() != 32 || db.Stats().InUse != 0 {
		t.Fatal("concurrent query/pool bound")
	}
	// A persisted eligibility transition is observed, rather than cached admission.
	batch := fixture.Batches[3]
	if _, err = db.Exec(`UPDATE bakney_user SET is_active=false WHERE user_id=$1`, batch.Selected); err != nil {
		t.Fatal(err)
	}
	if got, err := selection.Select(context.Background(), batch.Identifier, batch.Identifier, "google"); got != nil || !errors.Is(err, ErrLoginCredentials) {
		t.Fatal("inactive transition ignored")
	}
	if _, err = db.Exec(`UPDATE bakney_user SET is_active=true WHERE user_id=$1`, batch.Selected); err != nil {
		t.Fatal(err)
	}
	if got, err := selection.Select(context.Background(), batch.Identifier, batch.Identifier, "google"); err != nil || got == nil || got.ID != batch.Selected {
		t.Fatal("restored eligibility ignored")
	}
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	before := counter.count.Load()
	if got, err := selection.Select(ctx, batch.Identifier, batch.Identifier, "google"); got != nil || !errors.Is(err, context.Canceled) || counter.count.Load() != before {
		t.Fatal("cancelled selection queried")
	}
	if err = db.Close(); err != nil {
		t.Fatal(err)
	}
	if got, err := selection.Select(context.Background(), batch.Identifier, batch.Identifier, "google"); got != nil || err == nil {
		t.Fatal("database failure admitted")
	}
}
