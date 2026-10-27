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

type SavedReportStore interface {
	ActiveAssociationForUser(ctx context.Context, user string) (string, error)
	SavedReports(ctx context.Context, association, creator string) ([]SavedReport, error)
	SavedReport(ctx context.Context, id, association, creator string) (SavedReport, error)
	InsertSavedReport(ctx context.Context, id, association, creator string, write SavedReportWrite) error
	UpdateSavedReport(ctx context.Context, id string, change SavedReportChange) error
	DeleteSavedReport(ctx context.Context, id string) error
}
