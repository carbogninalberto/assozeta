package domain

import (
	"context"
	"errors"
	"time"
)

// ErrModulesExportServer is an exception modules_response_export does not handle (the 500 page).
var ErrModulesExportServer = errors.New("modules export unhandled error")

// ModuleExportResponse is one ModuleResponses row read by modules_response_export.
type ModuleExportResponse struct {
	ID        string    `db:"module_response_id"`
	ModuleID  string    `db:"module_id"`
	Created   time.Time `db:"creation_date"`
	Number    *int64    `db:"progressive_response_number"`
	Queue     *int64    `db:"queue_position"`
	Response  string    `db:"response"`
	Approved  bool      `db:"approved"`
	UserID    *string   `db:"user_id"`
	PaymentID *string   `db:"payment_id"`
}

// ModuleExportUser is the response user's name fields.
type ModuleExportUser struct {
	FirstName *string `db:"first_name"`
	LastName  *string `db:"last_name"`
}

type ModulesExportStore interface {
	// OwnerAssociation is request.user.sport_association for a non-athlete
	// (ErrAssociationNotFound without a live one).
	OwnerAssociation(ctx context.Context, user string) (string, error)
	// Responses is ModuleResponses.objects.filter(module_id=..., module__sport_association=...),
	// in the database's order (Django's queryset has no ordering).
	Responses(ctx context.Context, module, association string) ([]ModuleExportResponse, error)
	// FirstResponse is the same queryset's first() (ordered by primary key).
	FirstResponse(ctx context.Context, module, association string) (*ModuleExportResponse, error)
	// Module is the response's module title and elements (jsonb text).
	Module(ctx context.Context, id string) (title *string, elements *string, err error)
	// User reads a response's user through the base manager.
	User(ctx context.Context, id string) (*ModuleExportUser, error)
	// PaymentPaid reads a response's payment through the base manager.
	PaymentPaid(ctx context.Context, id string) (bool, error)
	// Attachments are r.attachments.all() filenames in the database's order.
	Attachments(ctx context.Context, response string) ([]string, error)
}
