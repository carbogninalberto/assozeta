package domain

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrAdminUpdateNotFound is the view's bare 404 (no active association or
	// no billing subscription for its owner).
	ErrAdminUpdateNotFound = errors.New("admin update target not found")
	// ErrAdminUpdateNotes is the caught exception while saving notes (400).
	ErrAdminUpdateNotes = errors.New("Errore durante l'aggiornamento delle note.")
)

type AdminBilling struct {
	ID          string     `db:"billing_subscription_id"`
	AutoRenewal bool       `db:"auto_renewal"`
	RenewalType int64      `db:"renewal_type"`
	EndsOn      *time.Time `db:"ends_on"`
	BillingPlan *string    `db:"billing_plan_id"`
}

type AdminBillingWrite struct {
	AutoRenewal bool
	RenewalType int64
	EndsOn      *time.Time
	BillingPlan *string
}

// AdminAudit is one auditlog update entry written before its save.
type AdminAudit struct {
	Model, ObjectID, Representation string
	Changes                         map[string][2]string
	Context                         LoginAuditContext
	AssociationID, ResolutionPath   *string
	At                              time.Time
}

type AssociationAdminStore interface {
	BeginAssociationAdmin(context.Context) (AssociationAdminTransaction, error)
}

type AssociationAdminTransaction interface {
	// AdminAssociation locks an active association (bounded by scope) and
	// returns its owner and notes.
	AdminAssociation(ctx context.Context, id string, scope *string) (owner string, notes *string, err error)
	FirstBillingForUpdate(ctx context.Context, user string) (AdminBilling, error)
	ActiveAssociationOf(ctx context.Context, user string) (*string, error)
	// AppendAdminAudit and the two updates each run in their own savepoint;
	// a failure rolls back only that step.
	AppendAdminAudit(context.Context, AdminAudit) error
	UpdateBilling(ctx context.Context, id string, write AdminBillingWrite) error
	UpdateNotes(ctx context.Context, association string, notes *string) error
	Commit() error
	Rollback() error
}
