package repository

import (
	"context"
	"database/sql"
	"database/sql/driver"
	"errors"
	"fmt"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
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
	listClose    func(context.Context) error
	queryErr     error
	rowErr       error
	listRows     *messageReadsRows
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

func (c *messageReadsSQLConn) QueryContext(ctx context.Context, query string, args []driver.NamedValue) (driver.Rows, error) {
	c.d.mu.Lock()
	c.d.queries = append(c.d.queries, query)
	c.d.args = append(c.d.args, append([]driver.NamedValue(nil), args...))
	c.d.mu.Unlock()
	switch {
	case strings.Contains(query, "SELECT sport_association_id FROM application_sportassociation"):
		return &messageReadsRows{columns: []string{"sport_association_id"}, values: [][]driver.Value{{c.d.association}}}, nil
	case strings.Contains(query, "FROM communications_message m"):
		if c.d.queryErr != nil {
			return nil, c.d.queryErr
		}
		c.d.listRows = &messageReadsRows{columns: []string{"message_id", "sport_association_id", "type", "message", "subject", "created_at"}, values: c.d.list, err: c.d.rowErr, close: func() error {
			if c.d.listClose != nil {
				return c.d.listClose(ctx)
			}
			return nil
		}}
		return c.d.listRows, nil
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
	err     error
	closed  bool
	close   func() error
}

func (r *messageReadsRows) Columns() []string { return r.columns }
func (r *messageReadsRows) Close() error {
	r.closed = true
	if r.close != nil {
		return r.close()
	}
	return nil
}
func (r *messageReadsRows) Next(destination []driver.Value) error {
	if r.at >= len(r.values) {
		if r.err != nil {
			return r.err
		}
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
	list, err := collectMessageReads(context.Background(), repo, owner)
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

// Collection is test-only: production receives and discards one record at a time.
func collectMessageReads(ctx context.Context, repo *MessageReads, owner string) ([]domain.MessageReadRecord, error) {
	rows := make([]domain.MessageReadRecord, 0)
	err := repo.VisitMessages(ctx, owner, func(row domain.MessageReadRecord) error { rows = append(rows, row); return nil })
	return rows, err
}

func TestMessageReadsVisitFailuresCloseCursorAndReleasePool(t *testing.T) {
	sentinel := errors.New("injected failure")
	for _, kind := range []string{"success", "query", "scan", "iteration", "consumer", "cancel", "already canceled"} {
		t.Run(kind, func(t *testing.T) {
			d := &messageReadsSQLDriver{association: "a1000000-0000-4000-8000-000000701001"}
			for i := 0; i < 4; i++ {
				d.list = append(d.list, []driver.Value{"a1000000-0000-4000-8000-000000702001", d.association, "EMAIL", "body", nil, nil})
			}
			if kind == "query" {
				d.queryErr = sentinel
			}
			if kind == "scan" {
				d.list[2][0] = "invalid-uuid"
			}
			if kind == "iteration" {
				d.rowErr = sentinel
			}
			db := messageReadsTestDB(t, d)
			db.SetMaxOpenConns(1)
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			if kind == "already canceled" {
				cancel()
			}
			calls := 0
			err := NewMessageReads(db).VisitMessages(ctx, "trusted-owner", func(domain.MessageReadRecord) error {
				calls++
				if calls == 2 {
					if kind == "consumer" {
						return sentinel
					}
					if kind == "cancel" {
						cancel()
					}
				}
				return nil
			})
			switch kind {
			case "success":
				if err != nil || calls != 4 {
					t.Fatalf("%d %v", calls, err)
				}
			case "scan":
				if err == nil || calls != 2 {
					t.Fatalf("scan %d %v", calls, err)
				}
			case "cancel", "already canceled":
				if !errors.Is(err, context.Canceled) {
					t.Fatal(err)
				}
			default:
				if !errors.Is(err, sentinel) {
					t.Fatal(err)
				}
			}
			if kind == "consumer" || kind == "cancel" {
				if calls != 2 {
					t.Fatalf("iteration continued after failure: %d", calls)
				}
			}
			if d.listRows != nil && !d.listRows.closed {
				t.Fatal("cursor not closed")
			}
			if db.Stats().InUse != 0 {
				t.Fatal("connection not released")
			}
			if kind == "already canceled" {
				if len(d.queries) != 0 {
					t.Fatal(d.queries)
				}
				return
			}
			if len(d.queries) != 2 || d.queries[1] != messageReadsListSQL || d.args[1][0].Value != d.association {
				t.Fatal("query contract changed", d.queries, d.args)
			}
			follow, stop := context.WithTimeout(context.Background(), time.Second)
			defer stop()
			if err := db.PingContext(follow); err != nil {
				t.Fatal("pool not reusable", err)
			}
		})
	}
}

func TestMessageReadsConsumerFailureCancelsBeforeCursorClose(t *testing.T) {
	consumerErr := errors.New("consumer failed")
	closed := make(chan error, 1)
	var queryContext context.Context
	d := &messageReadsSQLDriver{association: "a1000000-0000-4000-8000-000000701001"}
	d.list = [][]driver.Value{{"a1000000-0000-4000-8000-000000702001", d.association, "EMAIL", "body", nil, nil}}
	d.listClose = func(ctx context.Context) error { queryContext = ctx; closed <- ctx.Err(); return nil }
	db := messageReadsTestDB(t, d)
	parent := context.Background()
	err := NewMessageReads(db).VisitMessages(parent, "trusted-owner", func(domain.MessageReadRecord) error { return consumerErr })
	if !errors.Is(err, consumerErr) {
		t.Fatal("error identity lost", err)
	}
	if closeContextErr := <-closed; !errors.Is(closeContextErr, context.Canceled) {
		t.Fatal("unread rows closed before query cancellation", closeContextErr)
	}
	if queryContext == parent || parent.Err() != nil || db.Stats().InUse != 0 {
		t.Fatal("private context/connection lifetime lost")
	}
}
