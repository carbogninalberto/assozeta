package domain

import (
	"context"
	"errors"
	"time"
)

var ErrSavedReportNotFound = errors.New("Report not found")

type SavedReport struct {
	ID          string    `db:"saved_report_id"`
	Name        string    `db:"name"`
	Description string    `db:"description"`
	ToolName    string    `db:"tool_name"`
	Params      string    `db:"params"`    // jsonb text
	UIConfig    string    `db:"ui_config"` // jsonb text
	CreatedAt   time.Time `db:"created_at"`
	UpdatedAt   time.Time `db:"updated_at"`
}

type SavedReportWrite struct {
	Name, Description, ToolName string
	Params, UIConfig            string
	At                          time.Time
}

// SavedReportChange holds the assigned fields; nil leaves a field unchanged.
type SavedReportChange struct {
	Name, Description, Params, UIConfig *string
	At                                  time.Time
}

// ReportStatement is one read-only report query: the first Text columns are
// text renderings (NULL: nil), the next Hidden columns only order a DISTINCT.
type ReportStatement struct {
	SQL          string
	Args         []any
	Text, Hidden int
}

// ReportQueryError is a database error raised by a report query.
type ReportQueryError struct{ Err error }

func (e *ReportQueryError) Error() string { return "report query failed: " + e.Err.Error() }
func (e *ReportQueryError) Unwrap() error { return e.Err }

type SavedReportStore interface {
	// ReportIntegration reads the first instance configuration (if any).
	ReportIntegration(ctx context.Context) (selfHosted bool, settings *string, err error)
	// RunReportStatements runs the statements in one read-only transaction
	// with the statement timeout, returning each statement's text rows.
	RunReportStatements(ctx context.Context, timeoutMS int64, statements []ReportStatement) ([][][]*string, error)
	ActiveAssociationForUser(ctx context.Context, user string) (string, error)
	SavedReports(ctx context.Context, association, creator string) ([]SavedReport, error)
	SavedReport(ctx context.Context, id, association, creator string) (SavedReport, error)
	InsertSavedReport(ctx context.Context, id, association, creator string, write SavedReportWrite) error
	UpdateSavedReport(ctx context.Context, id string, change SavedReportChange) error
	DeleteSavedReport(ctx context.Context, id string) error
}
