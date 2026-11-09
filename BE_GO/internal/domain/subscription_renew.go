package domain

import (
	"context"
	"time"
)

// SubscriptionCreateFailure is a Python exception create_subscription raises
// whose str() is known: subscription/add answers the 500 page (it is
// ErrSubscriptionCreateServer), subscription/renew 400 {"status": "error",
// "msg": Text}.
type SubscriptionCreateFailure struct{ Text string }

func (e *SubscriptionCreateFailure) Error() string { return "subscription create failure: " + e.Text }

func (e *SubscriptionCreateFailure) Is(target error) bool {
	return target == ErrSubscriptionCreateServer
}

// SubscriptionRenewAssociateStore is AssociateSerializer.create's associate_id
// branch (subscription-renew.md).
type SubscriptionRenewAssociateStore interface {
	// AssociateAny is Associate.objects.all_objects().filter(associate_id=id).first().
	AssociateAny(ctx context.Context, id string) (*SubscriptionEditAssociate, error)
	// AssociateByTaxCode is all_objects().filter(tax_code__iexact, sport_association).first() (by key).
	AssociateByTaxCode(ctx context.Context, taxCode, association string) (*SubscriptionEditAssociate, error)
	// LiveTaxCodeTaken reports a live associate of the association, other than
	// except, whose UPPER(tax_code) matches (the partial unique index).
	LiveTaxCodeTaken(ctx context.Context, taxCode, association, except string) (bool, error)
	// SetAssociateDeleted writes the deleted column.
	SetAssociateDeleted(ctx context.Context, id string, deleted bool) error
	// NamedAssociations are the live associations of the live user with this username.
	NamedAssociations(ctx context.Context, username string) ([]string, error)
	// AssociationCount counts SportAssociation.objects.filter(user=user).
	AssociationCount(ctx context.Context, user string) (int, error)
}

// SubscriptionRenewTransaction is create_subscription plus the renewal's
// post-processing in one transaction.
type SubscriptionRenewTransaction interface {
	SubscriptionCreateTransaction
	// RenewalPayments are the live unpaid subscription payments of the
	// association whose associate matches (first and last name exact, tax
	// code iexact; nil is NULL) created in [start, end] (midnight UTC), except
	// one, limited to scope when set. invoiced reports one with an invoice.
	RenewalPayments(ctx context.Context, association string, first, last, tax *string, start, end time.Time, except, scope *string) (ids []string, invoiced bool, err error)
	// DeletePayments is the payments' QuerySet.delete() (signals and audit).
	DeletePayments(ctx context.Context, ids []string, audit CourseSubscriptionAudit) error
	// UserByEmail is User.objects.filter(email=email).first() (nil email: IS NULL).
	UserByEmail(ctx context.Context, email *string) (*string, error)
	SetSubscriptionUser(ctx context.Context, subscription, user string) error
	// AddTags links the existing tags among ids to the subscription.
	AddTags(ctx context.Context, subscription string, ids []string) error
}

type SubscriptionRenewStore interface {
	BeginSubscriptionRenew(ctx context.Context) (SubscriptionRenewTransaction, error)
}

// SubscriptionDraftApproveRow is an AssociateImportDraft row (data as stored text).
type SubscriptionDraftApproveRow struct {
	ID   string  `db:"associate_import_draft_id"`
	Data *string `db:"data"`
}

// SubscriptionDraftApproveTransaction is subscription_associates_draft_approve's
// work in one transaction (a savepoint per draft).
type SubscriptionDraftApproveTransaction interface {
	SubscriptionCreateTransaction
	Savepoint(ctx context.Context) error
	RollbackSavepoint(ctx context.Context) error
	ReleaseSavepoint(ctx context.Context) error
	// DraftCount counts the association's drafts (all, or those in ids).
	DraftCount(ctx context.Context, association string, all bool, ids []string) (int, error)
	// ValidDrafts are the valid ones in the table's order (no ORDER BY).
	ValidDrafts(ctx context.Context, association string, all bool, ids []string) ([]SubscriptionDraftApproveRow, error)
	// DeleteApprovingStatuses is the association's APPROVING statuses' QuerySet.delete().
	DeleteApprovingStatuses(ctx context.Context, association string, audit LoginAuditContext, at time.Time) error
	CreateApprovingStatus(ctx context.Context, association string, audit LoginAuditContext, at time.Time) error
	// DeleteDraft deletes the draft with an entry whose data renders as dataRepr.
	DeleteDraft(ctx context.Context, id, dataRepr string, audit LoginAuditContext, at time.Time) error
	// ValidateBroadcast is validate_broadcast's rows written in this transaction.
	ValidateBroadcast(ctx context.Context, user, name string, audit LoginAuditContext, at time.Time) (bool, error)
}

type SubscriptionDraftApproveStore interface {
	BeginSubscriptionDraftApprove(ctx context.Context) (SubscriptionDraftApproveTransaction, error)
}
