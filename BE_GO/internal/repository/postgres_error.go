package repository

import (
	"context"
	"errors"
	"strings"

	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jmoiron/sqlx"
)

// psycopg2ErrorText is str() of the exception psycopg2 raises for a server
// message: libpq's text without the severity prefix, so the message line, then
// one line per field the server sent (DETAIL, HINT, CONTEXT), each indented
// with the two spaces libpq uses, and a trailing newline.
func psycopg2ErrorText(err *pgconn.PgError) string {
	text := strings.Builder{}
	text.WriteString(err.Message)
	text.WriteString("\n")
	for _, field := range [][2]string{{"DETAIL", err.Detail}, {"HINT", err.Hint}, {"CONTEXT", err.Where}} {
		if field[1] == "" {
			continue
		}
		text.WriteString(field[0])
		text.WriteString(":  ")
		text.WriteString(field[1])
		text.WriteString("\n")
	}
	return text.String()
}

// campsPaymentSavepoint names the savepoint a rejected payment statement runs
// in.
const campsPaymentSavepoint = "camps_rejected_payment"

// runRejectedPayment runs statement inside a savepoint of the request's
// transaction and rolls the savepoint back: Django's Payment.save() leaves
// nothing behind when the row is refused, while the earlier writes of the
// request stay. The text is psycopg2's exception text for the server's answer;
// ok is false when the database accepted the statement.
func runRejectedPayment(ctx context.Context, tx *sqlx.Tx, statement func() error) (string, bool, error) {
	// Django's connection is UTC with ISO dates (Django's postgresql backend
	// sets the session TimeZone from USE_TZ/TIME_ZONE, both UTC here), and the
	// text PostgreSQL dumps for a rejected row renders timestamptz values in
	// the session time zone. Put the transaction on the same settings before
	// the savepoint so they also outlive its rollback.
	if _, err := tx.ExecContext(ctx, `SELECT set_config('TimeZone','UTC',true), set_config('DateStyle','ISO, MDY',true)`); err != nil {
		return "", false, err
	}
	if _, err := tx.ExecContext(ctx, `SAVEPOINT `+campsPaymentSavepoint); err != nil {
		return "", false, err
	}
	statementErr := statement()
	// A refused statement leaves the transaction aborted; either way the save
	// did not happen, so undo it and drop the savepoint.
	if _, err := tx.ExecContext(ctx, `ROLLBACK TO SAVEPOINT `+campsPaymentSavepoint); err != nil {
		return "", false, err
	}
	if _, err := tx.ExecContext(ctx, `RELEASE SAVEPOINT `+campsPaymentSavepoint); err != nil {
		return "", false, err
	}
	if statementErr == nil {
		return "", false, nil
	}
	var serverError *pgconn.PgError
	if !errors.As(statementErr, &serverError) {
		return "", false, statementErr
	}
	return psycopg2ErrorText(serverError), true, nil
}
