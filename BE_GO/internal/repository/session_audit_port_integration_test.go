//go:build sessionhttpintegration

package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"os"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

func TestSessionAuditPortPersistence(t *testing.T) {
	db, err := sqlx.Connect("pgx", os.Getenv("ASSOZETA_ACCOUNT_TEST_DSN"))
	if err != nil {
		t.Fatal("private database unavailable")
	}
	defer db.Close()
	for _, tc := range []struct {
		port   string
		reject bool
	}{{"", false}, {"0", false}, {"443", false}, {"2147483647", false}, {"-1", true}, {"2147483648", true}, {"1208925819614629174706176", true}} {
		t.Run(tc.port, func(t *testing.T) {
			tx, err := db.BeginTxx(context.Background(), nil)
			if err != nil {
				t.Fatal(err)
			}
			defer tx.Rollback()
			var port *json.Number
			if tc.port != "" {
				value := json.Number(tc.port)
				port = &value
			}
			err = appendModelAudit(context.Background(), tx, modelAuditEntry{Model: "user", Key: "00000000-0000-4000-8000-000000000001", Representation: "session-port-fixture", Action: 1, Changes: map[string][2]string{"username": {"before", "after"}}, Context: domain.LoginAuditContext{RemotePort: port}, At: time.Now().UTC()})
			if (err != nil) != tc.reject {
				t.Fatal("database port range differs")
			}
			if !tc.reject {
				var got sql.NullString
				if err := tx.Get(&got, `SELECT remote_port::text FROM auditlog_logentry WHERE object_repr='session-port-fixture' ORDER BY id DESC LIMIT 1`); err != nil || got.Valid != (tc.port != "") || got.String != tc.port {
					t.Fatal("persisted audit port differs")
				}
			}
		})
	}
}
