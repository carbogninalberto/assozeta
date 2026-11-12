package domain

import (
	"context"
	"encoding/json"
)

type ProfileTablePreferences struct {
	ID        string          `db:"user_id"`
	FirstName string          `db:"first_name"`
	LastName  string          `db:"last_name"`
	Tables    json.RawMessage `db:"tables_settings"`
}

type ProfileTablesStore interface {
	BeginProfileTables(context.Context) (ProfileTablesTransaction, error)
}

type ProfileTablesTransaction interface {
	TablePreferencesForUpdate(context.Context, string) (ProfileTablePreferences, error)
	SetTablePreferences(context.Context, string, json.RawMessage) error
	AuditAssociations(context.Context, string, *string) (map[string]string, error)
	AppendProfileAudit(context.Context, ProfileUpdateAudit) error
	Commit() error
	Rollback() error
}
