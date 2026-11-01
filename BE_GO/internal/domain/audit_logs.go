package domain

import (
	"context"
	"database/sql"
	"time"
)

// AuditLogRow is one auditlog LogEntry with its content type model and actor.
// JSON columns are the stored jsonb text (nil for SQL NULL).
type AuditLogRow struct {
	ID              int64          `db:"id"`
	Action          int64          `db:"action"`
	Timestamp       time.Time      `db:"timestamp"`
	ObjectPK        string         `db:"object_pk"`
	ObjectRepr      string         `db:"object_repr"`
	Model           string         `db:"model"`
	ActorID         sql.NullString `db:"actor_id"`
	ActorFirstName  sql.NullString `db:"actor_first_name"`
	ActorLastName   sql.NullString `db:"actor_last_name"`
	ActorEmail      sql.NullString `db:"actor_user_email"`
	EntryActorEmail sql.NullString `db:"actor_email"`
	RemoteAddr      sql.NullString `db:"remote_addr"`
	Changes         sql.NullString `db:"changes"`
	AdditionalData  sql.NullString `db:"additional_data"`
	SerializedData  sql.NullString `db:"serialized_data"`
}

// AuditLogQuery is audit_log_list's filtered queryset. Nil filters are not
// applied; Models are already stripped and lowercased.
type AuditLogQuery struct {
	AssociationID string
	Search        *string
	Action        *int64
	Models        []string
	ActorID       *string
	ObjectPK      *string
	From, To      *time.Time
	// OrderColumn is timestamp, action or model.
	OrderColumn string
	Descending  bool
}

// AuditObject is one row a serializer resolver reads through a model's
// default manager: its text columns by name (nil for NULL).
type AuditObject map[string]*string

type AuditLogStore interface {
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	CountEntries(ctx context.Context, query AuditLogQuery) (int64, error)
	Entries(ctx context.Context, query AuditLogQuery, limit, offset int64) ([]AuditLogRow, error)
	// IndexedEntry is the entry indexed to the association, or nil.
	IndexedEntry(ctx context.Context, association string, id int64) (*AuditLogRow, error)
	// AuditObject reads kind's row by primary key through its default manager
	// (group is the X-Group-Id filter of group-aware managers); nil if absent.
	AuditObject(ctx context.Context, kind, id string, group *string) (AuditObject, error)
	// EventLists are the events JSON texts of every AttendanceRegistry and/or
	// GlobalCalendarEvents row with a non-NULL events column, in table order.
	EventLists(ctx context.Context, registries, calendars bool) (registryEvents, calendarEvents []string, err error)
}
