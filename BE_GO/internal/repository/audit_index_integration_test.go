//go:build auditindexintegration

package repository_test

import (
	"bufio"
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/jmoiron/sqlx"
)

// TestAuditIndexResolverDriver serves TESTS/adapters/go/audit_index.py: each
// stdin line {"entry": id, "group": header} is answered on stdout with the
// row IndexAuditLogEntry writes for that entry once Django's own index row is
// removed, inside a transaction that is rolled back.
func TestAuditIndexResolverDriver(t *testing.T) {
	dsn := os.Getenv("ASSOZETA_AUDIT_INDEX_DSN")
	if !strings.Contains(dsn, "dbname=assozeta_contract_audit_index") {
		t.Fatal("private audit index database required")
	}
	db, err := sqlx.Open("pgx", dsn)
	if err != nil {
		t.Fatal(err)
	}
	defer db.Close()
	ctx := context.Background()
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		var request struct {
			Entry int64  `json:"entry"`
			Group string `json:"group"`
		}
		if err = json.Unmarshal(scanner.Bytes(), &request); err != nil {
			t.Fatal(err)
		}
		answer, err := indexOnce(ctx, db, request.Entry, request.Group)
		if err != nil {
			t.Fatal(err)
		}
		encoded, _ := json.Marshal(answer)
		fmt.Fprintf(os.Stdout, "AUDITINDEX %s\n", encoded)
	}
	if err = scanner.Err(); err != nil {
		t.Fatal(err)
	}
}

func indexOnce(ctx context.Context, db *sqlx.DB, entry int64, group string) (any, error) {
	tx, err := db.BeginTxx(ctx, nil)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, `DELETE FROM application_auditlogindex WHERE log_entry_id=$1`, entry); err != nil {
		return nil, err
	}
	at := time.Now()
	resolution, err := repository.IndexAuditLogEntry(ctx, tx, entry, group, at)
	if err != nil {
		return nil, err
	}
	var row struct {
		Association string         `db:"sport_association_id"`
		Path        sql.NullString `db:"resolution_path"`
	}
	err = tx.GetContext(ctx, &row, `SELECT sport_association_id::text, resolution_path FROM application_auditlogindex WHERE log_entry_id=$1`, entry)
	if err == sql.ErrNoRows {
		if resolution != nil {
			return nil, fmt.Errorf("resolution without a row for %d", entry)
		}
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return map[string]any{"association": row.Association, "path": row.Path.String}, nil
}
