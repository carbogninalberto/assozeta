package domain

import (
	"context"
	"encoding/json"
	"errors"
	"time"
)

// ErrPersonaSubscriptionNotFound is get_object_or_404's miss: "No Subscription matches the given query.".
var ErrPersonaSubscriptionNotFound = errors.New("subscription not found")

// PersonaSubscriptionRow is one AssociateSubscriptionViewSet row with the
// values its serializer reads: the medical certificate's expiration, the
// tags and has_future_renewal() (computed only when the row is not current).
type PersonaSubscriptionRow struct {
	ID                  string           `db:"subscription_id"`
	AssociationID       string           `db:"sport_association_id"`
	AssociateID         *string          `db:"associate_id"`
	SupplierID          *string          `db:"supplier_id"`
	InstructorID        *string          `db:"instructor_id"`
	CustomData          *json.RawMessage `db:"custom_data"`
	AdditionalFields    *json.RawMessage `db:"additional_fields"`
	UserID              string           `db:"user_id"`
	Type                int64            `db:"type"`
	Role                int64            `db:"role"`
	AcceptanceDate      *time.Time       `db:"acceptance_date"`
	ResignationDate     *time.Time       `db:"resignation_date"`
	MedicalID           *string          `db:"medical_id"`
	MedicalExpiration   *time.Time       `db:"medical_expiration"`
	StatusFlag          int64            `db:"status_flag"`
	CreationDate        time.Time        `db:"creation_date"`
	StartDate           *time.Time       `db:"start_date"`
	EndDate             *time.Time       `db:"end_date"`
	SignatureURL        *string          `db:"signature_url"`
	SignatureStorageKey *string          `db:"signature_storage_key"`
	DocumentID          *string          `db:"document_pdf_id"`
	PaymentID           *string          `db:"payment_id"`
	Archived            bool             `db:"archived"`
	Draft               bool             `db:"draft"`
	Trial               bool             `db:"trial"`
	Meta                *json.RawMessage `db:"meta"`
	SubscriptionNumber  *string          `db:"subscription_number"`
	SubscriptionType    *string          `db:"subscription_type"`
	GroupID             *string          `db:"group_id"`
	Deleted             bool             `db:"deleted"`
	Competitive         bool             `db:"competitive"`
	Notes               *string          `db:"notes"`
	Tags                *string          `db:"tags"`
	FutureRenewal       bool             `db:"future_renewal"`
}

// PersonaSubscriptionQuery is get_queryset().filter(associate_id=...).
type PersonaSubscriptionQuery struct {
	AssociationID, AssociateID string
	GroupID                    *string
	Today                      time.Time
}

type PersonaSubscriptionsStore interface {
	CountSubscriptions(ctx context.Context, query PersonaSubscriptionQuery) (int64, error)
	Subscriptions(ctx context.Context, query PersonaSubscriptionQuery, limit, offset int64) ([]PersonaSubscriptionRow, error)
	// SubscriptionVisible is get_object() on get_queryset() (association, not
	// deleted, group-aware).
	SubscriptionVisible(ctx context.Context, id, association string, group *string) (bool, error)
}
