package repository

import (
	"context"
	"database/sql/driver"
	"errors"
	"strings"
	"testing"
)

func TestInstanceUpdateAuditIndexFailurePreservesTransaction(t *testing.T) {
	var auditWritten, indexAttempted, aborted bool
	var savepoint string
	d := &rowRaceDriver{
		queryFn: func(query string, _ []driver.NamedValue) ([]string, [][]driver.Value, error) {
			switch {
			case strings.Contains(query, "INSERT INTO auditlog_logentry"):
				auditWritten = true
				return []string{"id"}, [][]driver.Value{{int64(1)}}, nil
			case strings.Contains(query, "FROM application_auditlogindex"):
				return []string{"exists"}, [][]driver.Value{{false}}, nil
			case strings.Contains(query, "FROM application_sportassociation"):
				return []string{"sport_association_id"}, [][]driver.Value{{"00000000-0000-4000-8000-000000000001"}}, nil
			}
			t.Fatalf("unexpected query: %s", query)
			return nil, nil, nil
		},
		execFn: func(query string, _ []driver.NamedValue) (driver.Result, error) {
			sql := strings.ToUpper(strings.TrimSpace(strings.TrimSuffix(query, ";")))
			words := strings.Fields(sql)
			switch {
			case strings.HasPrefix(sql, "SAVEPOINT "):
				if !auditWritten || aborted {
					t.Fatalf("savepoint must protect the index after the audit entry is written: %s", query)
				}
				savepoint = words[len(words)-1]
			case strings.HasPrefix(sql, "INSERT INTO APPLICATION_AUDITLOGINDEX"):
				indexAttempted, aborted = true, true
				return nil, errors.New("injected audit index failure")
			case strings.HasPrefix(sql, "ROLLBACK TO "):
				if !aborted || words[len(words)-1] != savepoint {
					t.Fatalf("unexpected rollback after index failure: %s", query)
				}
				aborted = false
			case strings.HasPrefix(sql, "RELEASE "):
				if aborted || words[len(words)-1] != savepoint {
					t.Fatalf("cannot release savepoint: %s", query)
				}
				savepoint = ""
			default:
				t.Fatalf("unexpected exec: %s", query)
			}
			return driver.RowsAffected(0), nil
		},
	}
	tx := rowRaceTestTransaction(t, d)
	if err := appendInstanceUpdateAudit(context.Background(), tx, InstanceBackupAction{}); err != nil {
		t.Fatalf("best-effort index failure was returned to the caller: %v", err)
	}
	if !auditWritten || !indexAttempted {
		t.Fatal("expected the audit entry and an attempted index insert")
	}
	if aborted {
		t.Fatal("failed index insert left the caller's transaction aborted")
	}
}
