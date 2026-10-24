//go:build integration

package service

import (
	"context"
	"encoding/json"
	"os"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	tokenjwt "github.com/carbogninalberto/assozeta/BE_GO/pkg/jwt"
	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

func TestActorAuthenticationAgainstDjangoCredentialsAndAccounts(t *testing.T) {
	path := os.Getenv("ASSOZETA_ACTOR_CREDENTIAL_FIXTURE")
	dsn := os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN")
	if path == "" || dsn == "" {
		t.Fatal("run through TESTS/adapters/django/accounts.py")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	// Explicit field tags retain fixture names without reflecting credentials in errors.
	var input struct {
		Private, Public string
		Cases           []struct {
			Name    string `json:"name"`
			Header  string `json:"header"`
			ActorID string `json:"actor_id"`
			Code    string `json:"code"`
		}
	}
	if err := json.Unmarshal(data, &input); err != nil {
		t.Fatal("invalid synthetic credential fixture")
	}
	if len(input.Cases) == 0 {
		t.Fatal("no actor cases")
	}
	tokens, err := tokenjwt.New([]byte(input.Private), []byte(input.Public), time.Now)
	if err != nil {
		t.Fatal(err)
	}
	db, err := sqlx.Connect("pgx", dsn)
	if err != nil {
		t.Fatal("private account database unavailable")
	}
	defer db.Close()
	auth := NewActorAuthentication(repository.NewAccounts(db), tokens)
	for _, tc := range input.Cases {
		t.Run(tc.Name, func(t *testing.T) {
			actor, err := auth.Authenticate(context.Background(), tc.Header)
			if authCode(err) != tc.Code || (err != nil && tc.Code == "") {
				t.Fatalf("authentication code = %q; want %q", authCode(err), tc.Code)
			}
			id := ""
			if actor != nil {
				id = actor.ID
			}
			if id != tc.ActorID {
				t.Fatal("authenticated wrong account")
			}
			if actor != nil && actor.Role == 3 && (actor.Superuser || actor.Email != "SHARED@example.test") {
				t.Fatal("claims replaced database role/email")
			}
		})
	}
}
