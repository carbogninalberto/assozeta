package domain

import (
	"context"
	"time"
)

// MedicalAppointmentAddRow is the MedicalAppointments instance the view saves;
// Changes is auditlog's create diff of the in-memory instance.
type MedicalAppointmentAddRow struct {
	ID, Subscription, Region string
	Date                     *string // ISO date
	Notes, Sport, Address    *string
	Meta                     *string // jsonb text; nil is SQL NULL
	CreationDate             time.Time
	Changes                  map[string][2]string
}

type MedicalAppointmentAddStore interface {
	BeginMedicalAppointmentAdd(context.Context) (MedicalAppointmentAddTransaction, error)
}

type MedicalAppointmentAddTransaction interface {
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	// Subscription is scoped_queryset(Subscription.objects) (group-aware, deleted rows included) by id and association.
	Subscription(ctx context.Context, id, association string, impersonation, group *string) (bool, error)
	// InsertAppointment saves the row and its auditlog create (index subscription.sport_association).
	InsertAppointment(ctx context.Context, row MedicalAppointmentAddRow, association string, audit LoginAuditContext, at time.Time) error
	Commit() error
	Rollback() error
}
