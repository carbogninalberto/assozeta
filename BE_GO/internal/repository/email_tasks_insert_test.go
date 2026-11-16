package repository

import (
	"context"
	"database/sql/driver"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

const (
	emailRaceConfigurationID = "a1000000-0000-4000-8000-000000000601"
	emailRaceAssociation     = "a1000000-0000-4000-8000-000000000602"
)

var emailConfigurationColumns = []string{"name", "type", "foreign_key"}

// emailConfigurationSelectColumns is the SELECT list of
// emailTaskConfigurationColumns, in order.
var emailConfigurationSelectColumns = []string{"communication_configuration_id", "sport_association_id", "email_smtp_host",
	"email_smtp_port", "email_smtp_user", "email_smtp_password", "email_sender_name", "email_encryption",
	"daily_email_limit", "daily_email_balance"}

var emailConfigurationColumnRows = [][]driver.Value{
	{"communication_configuration_id", "uuid", false},
	{"sport_association_id", "uuid", true},
	{"email_smtp_host", "character varying", false},
	{"email_smtp_port", "integer", false},
	{"email_smtp_user", "character varying", false},
	{"email_smtp_password", "character varying", false},
	{"email_sender_name", "character varying", false},
	{"email_encryption", "character varying", false},
	{"daily_email_limit", "integer", false},
	{"daily_email_balance", "integer", false},
}

func emailRaceConfigurationJSON(balance int) []byte {
	return []byte(fmt.Sprintf(`{"communication_configuration_id":%q,"sport_association_id":%q,"email_smtp_host":"smtp.example.test",`+
		`"email_smtp_port":587,"email_smtp_user":"user","email_smtp_password":"secret","email_sender_name":"Sender",`+
		`"email_encryption":"TLS","daily_email_limit":10,"daily_email_balance":%d}`,
		emailRaceConfigurationID, emailRaceAssociation, balance))
}

func emailRaceConfiguration(balance int64) domain.CommunicationConfiguration {
	association := uuid.MustParse(emailRaceAssociation)
	host, user, password, sender := "smtp.example.test", "user", "secret", "Sender"
	port := int64(587)
	return domain.CommunicationConfiguration{ID: uuid.MustParse(emailRaceConfigurationID), AssociationID: &association,
		Host: &host, Port: &port, User: &user, Password: &password, SenderName: &sender, Encryption: "TLS",
		DailyLimit: 10, DailyBalance: balance}
}

func emailRaceTransaction(t *testing.T, d *rowRaceDriver) *emailTaskTransaction {
	t.Helper()
	return &emailTaskTransaction{ownedTransaction: &ownedTransaction{tx: rowRaceTestTransaction(t, d)}}
}

// TestSaveCommunicationConfigurationUpdatesStoredRow: the row is still there,
// the UPDATE matches it and auditlog logs one update entry, after the write.
func TestSaveCommunicationConfigurationUpdatesStoredRow(t *testing.T) {
	stored := []driver.Value{emailRaceConfigurationID, emailRaceAssociation, "smtp.example.test", int64(587), "user", "secret",
		"Sender", "TLS", int64(10), int64(3)}
	d := &rowRaceDriver{
		queryFn: func(query string, _ []driver.NamedValue) ([]string, [][]driver.Value, error) {
			switch {
			case strings.Contains(query, "WHERE communication_configuration_id=$1 FOR UPDATE"):
				return emailConfigurationSelectColumns, [][]driver.Value{stored}, nil
			case strings.Contains(query, "INSERT INTO auditlog_logentry"):
				return []string{"id"}, [][]driver.Value{{int64(7)}}, nil
			}
			return nil, nil, fmt.Errorf("unexpected query: %s", query)
		},
		execFn: func(query string, _ []driver.NamedValue) (driver.Result, error) {
			switch {
			case strings.HasPrefix(query, "UPDATE communications_communicationconfiguration"):
				return driver.RowsAffected(1), nil
			case strings.HasPrefix(query, "INSERT INTO application_auditlogindex"):
				return driver.RowsAffected(1), nil
			}
			return nil, fmt.Errorf("unexpected exec: %s", query)
		},
	}
	tx := emailRaceTransaction(t, d)
	at := time.Date(2026, 3, 1, 10, 0, 0, 0, time.UTC)
	if err := tx.SaveCommunicationConfiguration(context.Background(), emailRaceConfiguration(4), at); err != nil {
		t.Fatal(err)
	}
	queries, execs := d.statements()
	if len(execs) != 2 || !strings.HasPrefix(execs[0].query, "UPDATE communications_communicationconfiguration") {
		t.Fatalf("expected the row update, got %#v", execs)
	}
	var entry struct {
		action  int64
		model   string
		app     string
		changes map[string][2]string
	}
	for _, call := range queries {
		if !strings.Contains(call.query, "INSERT INTO auditlog_logentry") {
			continue
		}
		entry.action = call.args[9].Value.(int64)
		entry.model = call.args[10].Value.(string)
		entry.app = call.args[12].Value.(string)
		if err := json.Unmarshal([]byte(call.args[2].Value.(string)), &entry.changes); err != nil {
			t.Fatal(err)
		}
	}
	if entry.action != 1 || entry.model != "communicationconfiguration" || entry.app != "communications" {
		t.Fatalf("update entry %+v", entry)
	}
	if len(entry.changes) != 1 || entry.changes["daily_email_balance"] != [2]string{"3", "4"} {
		t.Fatalf("update changes %#v", entry.changes)
	}
}

// TestSaveCommunicationConfigurationInsertsDeletedRow: Model.save() matched no
// row because a concurrent request deleted the configuration after it was
// read; Django inserts it again under the same primary key and auditlog logs a
// create entry (every audited field from None), not an update.
func TestSaveCommunicationConfigurationInsertsDeletedRow(t *testing.T) {
	d := &rowRaceDriver{
		queryFn: func(query string, _ []driver.NamedValue) ([]string, [][]driver.Value, error) {
			switch {
			case strings.Contains(query, "SELECT to_jsonb(t) FROM communications_communicationconfiguration"):
				return []string{"to_jsonb"}, [][]driver.Value{{emailRaceConfigurationJSON(7)}}, nil
			case strings.Contains(query, "FROM pg_attribute"):
				return emailConfigurationColumns, emailConfigurationColumnRows, nil
			case strings.Contains(query, "WHERE communication_configuration_id=$1 FOR UPDATE"):
				return emailConfigurationSelectColumns, nil, nil
			case strings.Contains(query, "INSERT INTO auditlog_logentry"):
				return []string{"id"}, [][]driver.Value{{int64(7)}}, nil
			}
			return nil, nil, fmt.Errorf("unexpected query: %s", query)
		},
		execFn: func(query string, _ []driver.NamedValue) (driver.Result, error) {
			switch {
			case strings.HasPrefix(query, "UPDATE communications_communicationconfiguration"):
				return driver.RowsAffected(0), nil
			case strings.HasPrefix(query, "INSERT INTO communications_communicationconfiguration"):
				return driver.RowsAffected(1), nil
			case strings.HasPrefix(query, "INSERT INTO application_auditlogindex"):
				return driver.RowsAffected(1), nil
			}
			return nil, fmt.Errorf("unexpected exec: %s", query)
		},
	}
	tx := emailRaceTransaction(t, d)
	at := time.Date(2026, 3, 1, 10, 0, 0, 0, time.UTC)
	if err := tx.SaveCommunicationConfiguration(context.Background(), emailRaceConfiguration(7), at); err != nil {
		t.Fatal(err)
	}
	queries, execs := d.statements()
	if len(execs) != 3 || !strings.HasPrefix(execs[1].query, "INSERT INTO communications_communicationconfiguration") {
		t.Fatalf("expected the row insert, got %#v", execs)
	}
	for _, column := range []string{"communication_configuration_id", "sport_association_id", "email_smtp_host", "email_smtp_port",
		"email_smtp_user", "email_smtp_password", "email_sender_name", "email_encryption", "daily_email_limit", "daily_email_balance"} {
		if !strings.Contains(execs[1].query, column) {
			t.Fatalf("insert must write every column, missing %s: %s", column, execs[1].query)
		}
	}
	entries := 0
	for _, call := range queries {
		if !strings.Contains(call.query, "INSERT INTO auditlog_logentry") {
			continue
		}
		entries++
		if action := call.args[9].Value.(int64); action != 0 {
			t.Fatalf("a create entry, not an update: action %d", action)
		}
		var changes map[string][2]string
		if err := json.Unmarshal([]byte(call.args[2].Value.(string)), &changes); err != nil {
			t.Fatal(err)
		}
		if _, excluded := changes["email_smtp_password"]; excluded {
			t.Fatal("exclude_fields=['email_smtp_password'] keeps the password out of the entry")
		}
		if changes["daily_email_balance"] != [2]string{"None", "7"} {
			t.Fatalf("create changes %#v", changes)
		}
		if changes["sport_association"] != [2]string{"None", emailRaceAssociation} {
			t.Fatalf("create changes %#v", changes)
		}
		if len(changes) != 9 {
			t.Fatalf("create changes count %d: %#v", len(changes), changes)
		}
	}
	if entries != 1 {
		t.Fatalf("one create entry, got %d", entries)
	}
}

// TestSaveCommunicationConfigurationInsertFailureIsReturned: the INSERT is an
// exception inside Django's try (e.g. the association was deleted too), so the
// caller answers send_email's (False, e) and nothing else is written.
func TestSaveCommunicationConfigurationInsertFailureIsReturned(t *testing.T) {
	sentinel := errors.New("foreign key violation")
	d := &rowRaceDriver{
		queryFn: func(query string, _ []driver.NamedValue) ([]string, [][]driver.Value, error) {
			if strings.Contains(query, "WHERE communication_configuration_id=$1 FOR UPDATE") {
				return emailConfigurationSelectColumns, nil, nil
			}
			return nil, nil, fmt.Errorf("unexpected query: %s", query)
		},
		execFn: func(query string, _ []driver.NamedValue) (driver.Result, error) {
			switch {
			case strings.HasPrefix(query, "UPDATE communications_communicationconfiguration"):
				return driver.RowsAffected(0), nil
			case strings.HasPrefix(query, "INSERT INTO communications_communicationconfiguration"):
				return nil, sentinel
			}
			return nil, fmt.Errorf("unexpected exec: %s", query)
		},
	}
	tx := emailRaceTransaction(t, d)
	err := tx.SaveCommunicationConfiguration(context.Background(), emailRaceConfiguration(7), time.Now())
	if !errors.Is(err, sentinel) {
		t.Fatalf("insert failure %v", err)
	}
	queries, _ := d.statements()
	for _, call := range queries {
		if strings.Contains(call.query, "INSERT INTO auditlog_logentry") {
			t.Fatal("a failed insert leaves no create entry")
		}
	}
}
