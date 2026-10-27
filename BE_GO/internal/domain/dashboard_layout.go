package domain

import (
	"context"
	"time"
)

// DashboardLayoutOwner is the locked user whose layout changes.
type DashboardLayoutOwner struct {
	FirstName string  `db:"first_name"`
	LastName  string  `db:"last_name"`
	Layout    *string `db:"dashboard_layout"` // jsonb text, nil for SQL NULL
}

type DashboardLayoutAudit struct {
	UserID, Representation string
	Changes                map[string][2]string
	Context                LoginAuditContext
	// AssociationID and ResolutionPath are nil when no resolver matches.
	AssociationID, ResolutionPath *string
	At                            time.Time
}

type DashboardLayoutStore interface {
	BeginDashboardLayout(context.Context) (DashboardLayoutTransaction, error)
}

type DashboardLayoutTransaction interface {
	AssociationForOwner(context.Context, string, *string) (string, error)
	// ActiveAssociationOf is SportAssociation.objects.filter(user=...).first().
	ActiveAssociationOf(ctx context.Context, user string) (*string, error)
	DashboardLayoutForUpdate(ctx context.Context, user string) (DashboardLayoutOwner, error)
	AppendDashboardLayoutAudit(context.Context, DashboardLayoutAudit) error
	// SetDashboardLayout runs in a savepoint; nil stores SQL NULL.
	SetDashboardLayout(ctx context.Context, user string, layout *string) error
	Commit() error
	Rollback() error
}

// DashboardLayoutRejected is a database rejection of the new value after the
// audit entry was written; the caller commits the audit and reports 500.
type DashboardLayoutRejected struct{ Err error }

func (e *DashboardLayoutRejected) Error() string {
	return "dashboard layout rejected: " + e.Err.Error()
}
func (e *DashboardLayoutRejected) Unwrap() error { return e.Err }
