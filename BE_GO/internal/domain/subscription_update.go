package domain

import (
	"context"
	"errors"
	"time"
)

var (
	// ErrSubscriptionUpdateNotFound is subscription_update's
	// PermissionDenied('Subscription not found.').
	ErrSubscriptionUpdateNotFound = errors.New("subscription update target not found")
	// ErrSubscriptionUpdateDenied is PermissionDenied('User not allowed.').
	ErrSubscriptionUpdateDenied = errors.New("subscription update not allowed")
)

// SubscriptionUpdateRow is the subscription subscription_update resolved:
// every column update_subscription assigns, document_pdf, and what
// Subscription.save() reads from the association owner's settings. JSON
// columns are the stored jsonb text (nil for SQL NULL).
type SubscriptionUpdateRow struct {
	ID            string     `db:"subscription_id"`
	AssociationID string     `db:"sport_association_id"`
	Type          int        `db:"type"`
	Role          int        `db:"role"`
	Status        int        `db:"status_flag"`
	Start         *time.Time `db:"start_date"`
	End           *time.Time `db:"end_date"`
	Acceptance    *time.Time `db:"acceptance_date"`
	Resignation   *time.Time `db:"resignation_date"`
	Number        *string    `db:"subscription_number"`
	Kind          *string    `db:"subscription_type"`
	Custom        *string    `db:"custom_data"`
	Additional    *string    `db:"additional_fields"`
	Notes         *string    `db:"notes"`
	DocumentID    *string    `db:"document_pdf_id"`
	Created       time.Time  `db:"creation_date"`
	StartDay      int        `db:"subscription_start_day"`
	StartMonth    int        `db:"subscription_start_month"`
	CustomEnd     bool       `db:"custom_end_date"`
	EndDay        int        `db:"subscription_end_day"`
	EndMonth      int        `db:"subscription_end_month"`
	Duration      int        `db:"subscription_duration"`
}

// SubscriptionUpdateSave is the Model.save() of the updated row: every
// column it writes (JSON as jsonb text, nil for SQL NULL) and the audited
// differences.
type SubscriptionUpdateSave struct {
	ID, AssociationID       string
	Type, Role, Status      int
	Start, End              *string
	Acceptance, Resignation *string
	Number, Kind, Notes     *string
	Custom, Additional      *string
	Changes                 map[string][2]string
}

// SubscriptionUpdateLookup is scoped_queryset(request, Subscription.objects):
// the impersonated association (nil otherwise), the X-Group-Id filter and, for
// athletes, the user filter.
type SubscriptionUpdateLookup struct {
	Scope, Group, User *string
}

type SubscriptionUpdateStore interface {
	BeginSubscriptionUpdate(ctx context.Context) (SubscriptionUpdateTransaction, error)
}

type SubscriptionUpdateTransaction interface {
	// OwnerAssociation is scoped_queryset(SportAssociation.objects).get(user=...).
	OwnerAssociation(ctx context.Context, owner string, scope *string) (string, error)
	ActorAssociation(ctx context.Context, actor string) (*string, error)
	// Subscription is the first matching row (nil when none), locked.
	Subscription(ctx context.Context, id string, lookup SubscriptionUpdateLookup) (*SubscriptionUpdateRow, error)
	SaveSubscription(ctx context.Context, save SubscriptionUpdateSave, audit CourseSubscriptionAudit) error
	// AuditOnly appends the pre_save entry of a save the database rejects.
	AuditOnly(ctx context.Context, save SubscriptionUpdateSave, audit CourseSubscriptionAudit) error
	Commit() error
	Rollback() error
}
