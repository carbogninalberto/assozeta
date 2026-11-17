package repository

import (
	"context"
	"database/sql"
	"errors"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jackc/pgx/v5/pgconn"
)

// statementTimeoutExec is the transaction's ExecContext: it records the text
// and answers like PostgreSQL's SET statement_timeout validation.
type statementTimeoutExec struct {
	query, text string
	err         error
}

func (e *statementTimeoutExec) ExecContext(_ context.Context, query string, args ...any) (sql.Result, error) {
	e.query, e.text = query, args[0].(string)
	return nil, e.err
}

// TestSetReportStatementTimeout is QueryBuilder._set_query_timeout's error
// mapping: PostgreSQL's rejection of the text (” for the empty stand-in,
// negatives, values beyond its range) is the tool's exception
// (ReportQueryError), so a saved-report run answers its JSON 500 and the MCP
// command the server message.
func TestSetReportStatementTimeout(t *testing.T) {
	server := &pgconn.PgError{Severity: "ERROR", Code: "22023",
		Message: `invalid value for parameter "statement_timeout": ""`}
	exec := &statementTimeoutExec{err: server}
	err := setReportStatementTimeout(context.Background(), exec, "")
	var query *domain.ReportQueryError
	if !errors.As(err, &query) || !errors.Is(err, server) {
		t.Fatalf("error %v, want a ReportQueryError wrapping the server error", err)
	}
	if exec.query != `SELECT set_config('statement_timeout', $1, true)` || exec.text != "" {
		t.Fatalf("executed %q with %q", exec.query, exec.text)
	}
	// A text PostgreSQL accepts is applied as sent.
	ok := &statementTimeoutExec{}
	if err := setReportStatementTimeout(context.Background(), ok, "10000"); err != nil || ok.text != "10000" {
		t.Fatalf("accepted text: %v, %q", err, ok.text)
	}
}
