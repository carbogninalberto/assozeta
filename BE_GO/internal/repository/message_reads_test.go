package repository

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"fmt"
	"io"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

var messageReadsDriverSequence uint64
var messageReadsDriverMu sync.Mutex

type messageReadsSQLDriver struct {
	association  string
	list, detail [][]driver.Value
	mu           sync.Mutex
	queries      []string
	args         [][]driver.NamedValue
}

func (d *messageReadsSQLDriver) Open(string) (driver.Conn, error) {
	return &messageReadsSQLConn{d: d}, nil
}

type messageReadsSQLConn struct{ d *messageReadsSQLDriver }

func (c *messageReadsSQLConn) Prepare(string) (driver.Stmt, error) { return nil, driver.ErrSkip }
func (c *messageReadsSQLConn) Close() error                        { return nil }
func (c *messageReadsSQLConn) Begin() (driver.Tx, error)           { return nil, driver.ErrSkip }

func (c *messageReadsSQLConn) QueryContext(_ context.Context, query string, args []driver.NamedValue) (driver.Rows, error) {
	c.d.mu.Lock()
	c.d.queries = append(c.d.queries, query)
	c.d.args = append(c.d.args, append([]driver.NamedValue(nil), args...))
	c.d.mu.Unlock()
	switch {
	case strings.Contains(query, "SELECT sport_association_id FROM application_sportassociation"):
		return &messageReadsRows{columns: []string{"sport_association_id"}, values: [][]driver.Value{{c.d.association}}}, nil
	case strings.Contains(query, "FROM communications_message m"):
		return &messageReadsRows{
			columns: []string{"message_id", "sport_association_id", "type", "message", "subject", "created_at"},
			values:  c.d.list,
		}, nil
	case strings.Contains(query, "FROM communications_messagetransaction t"):
		return &messageReadsRows{
			columns: []string{"message_transaction_id", "message_id", "recipient", "sent_on", "delivered_on", "opened_on", "clicked_on", "bounced_on", "bounced_reason", "bounced_type", "bounced_status"},
			values:  c.d.detail,
		}, nil
	default:
		return nil, fmt.Errorf("unexpected query: %s", query)
	}
}

type messageReadsRows struct {
	columns []string
	values  [][]driver.Value
	at      int
}

func (r *messageReadsRows) Columns() []string { return r.columns }
func (r *messageReadsRows) Close() error      { return nil }
func (r *messageReadsRows) Next(destination []driver.Value) error {
	if r.at >= len(r.values) {
		return io.EOF
	}
	copy(destination, r.values[r.at])
	r.at++
	return nil
}

func messageReadsTestDB(t *testing.T, d *messageReadsSQLDriver) *sqlx.DB {
	t.Helper()
	messageReadsDriverMu.Lock()
	messageReadsDriverSequence++
	name := fmt.Sprintf("message-reads-%d", messageReadsDriverSequence)
	messageReadsDriverMu.Unlock()
	sql.Register(name, d)
	db := sqlx.NewDb(sql.OpenDB(&messageReadsTestConnector{d: d}), name)
	t.Cleanup(func() { _ = db.Close() })
	return db
}

type messageReadsTestConnector struct{ d *messageReadsSQLDriver }

func (c *messageReadsTestConnector) Connect(context.Context) (driver.Conn, error) {
	return &messageReadsSQLConn{d: c.d}, nil
}
func (c *messageReadsTestConnector) Driver() driver.Driver { return c.d }

func TestMessageReadsRepositoryScopesOwnerAndHydratesTypedRows(t *testing.T) {
	owner := "a1000000-0000-4000-8000-000000700001"
	association := "a1000000-0000-4000-8000-000000701001"
	message := "a1000000-0000-4000-8000-000000702001"
	transaction := "a1000000-0000-4000-8000-000000703001"
	created := time.Date(2026, 10, 23, 8, 9, 10, 123456000, time.UTC)
	sent := time.Date(2026, 10, 23, 12, 0, 0, 123456000, time.UTC)
	d := &messageReadsSQLDriver{
		association: association,
		list: [][]driver.Value{{
			message, association, "EMAIL", "<p>A & B</p>", nil, created,
		}},
		detail: [][]driver.Value{{
			transaction, message, "person@example.test", sent, nil, nil, nil, nil, "Mailbox full", "permanent", "5.2.2",
		}},
	}
	repo := NewMessageReads(messageReadsTestDB(t, d))
	list, err := repo.ListMessages(context.Background(), owner)
	if err != nil || len(list) != 1 || list[0].ID != uuid.MustParse(message) ||
		list[0].AssociationID != uuid.MustParse(association) || list[0].Subject != nil ||
		list[0].CreatedAt == nil || !list[0].CreatedAt.Equal(created) {
		t.Fatalf("list %+v %v", list, err)
	}
	if len(d.queries) != 2 {
		t.Fatalf("list query count: %d %#v", len(d.queries), d.queries)
	}
	detail, err := repo.MessageTransactions(context.Background(), owner, message)
	if err != nil || len(detail) != 1 || detail[0].ID != uuid.MustParse(transaction) ||
		detail[0].MessageID != uuid.MustParse(message) || detail[0].DeliveredOn != nil ||
		detail[0].BouncedReason == nil || *detail[0].BouncedReason != "Mailbox full" {
		t.Fatalf("detail %+v %v", detail, err)
	}
	if len(d.queries) != 4 {
		t.Fatalf("detail query count: %d %#v", len(d.queries)-2, d.queries)
	}
	if d.args[0][0].Value != owner || d.args[1][0].Value != association ||
		d.args[2][0].Value != owner || d.args[3][0].Value != message || d.args[3][1].Value != association {
		t.Fatalf("message/transaction scope: %#v", d.args)
	}
	for _, query := range []string{d.queries[1], d.queries[3]} {
		if !strings.Contains(query, "m.type IN ('EMAIL', 'INSIDE_APP')") ||
			!strings.Contains(query, "m.sport_association_id=$") {
			t.Fatalf("missing type/association predicate: %s", query)
		}
	}
	if !strings.Contains(d.queries[1], "ORDER BY m.created_at DESC") ||
		!strings.Contains(d.queries[3], "ORDER BY t.sent_on DESC, t.message_transaction_id") {
		t.Fatal("Django ordering changed", d.queries)
	}
}
