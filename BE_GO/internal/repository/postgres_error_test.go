package repository

import (
	"testing"

	"github.com/jackc/pgx/v5/pgconn"
)

// psycopg2's str() of a server error: the message line, then one line per
// field libpq sent, each with two spaces after the label, and a final newline.
func TestPsycopg2ErrorTextLayout(t *testing.T) {
	tests := []struct {
		name   string
		server pgconn.PgError
		want   string
	}{
		{"message only",
			pgconn.PgError{Code: "22001", Message: "value too long for type character varying(5120)"},
			"value too long for type character varying(5120)\n"},
		{"message and detail",
			pgconn.PgError{Code: "23514", Message: `new row for relation "application_payment" violates check constraint "payment_amount_positive"`,
				Detail: "Failing row contains (7f000000-0000-4000-8000-000000000001, default, 0, -1.00, null)."},
			"new row for relation \"application_payment\" violates check constraint \"payment_amount_positive\"\n" +
				"DETAIL:  Failing row contains (7f000000-0000-4000-8000-000000000001, default, 0, -1.00, null).\n"},
		{"message, detail and hint",
			pgconn.PgError{Code: "23505", Message: "duplicate key value violates unique constraint \"payment_pkey\"",
				Detail: "Key (payment_id)=(7f000000-0000-4000-8000-000000000001) already exists.",
				Hint:   "Use another key."},
			"duplicate key value violates unique constraint \"payment_pkey\"\n" +
				"DETAIL:  Key (payment_id)=(7f000000-0000-4000-8000-000000000001) already exists.\n" +
				"HINT:  Use another key.\n"},
		{"context line",
			pgconn.PgError{Code: "23503", Message: "insert or update violates foreign key constraint",
				Detail: "Key (group_id)=(7f000000-0000-4000-8000-000000000002) is not present in table \"application_group\".",
				Where:  "SQL statement \"INSERT INTO application_payment ...\""},
			"insert or update violates foreign key constraint\n" +
				"DETAIL:  Key (group_id)=(7f000000-0000-4000-8000-000000000002) is not present in table \"application_group\".\n" +
				"CONTEXT:  SQL statement \"INSERT INTO application_payment ...\"\n"},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			server := tc.server
			if got := psycopg2ErrorText(&server); got != tc.want {
				t.Fatalf("text mismatch:\n got %q\nwant %q", got, tc.want)
			}
		})
	}
}
