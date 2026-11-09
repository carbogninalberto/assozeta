package domain

import (
	"context"
	"time"

	"github.com/google/uuid"
)

// JobAssociationQuoteAssign is the Celery name of association_quote_assign,
// as persisted Beat rows name it (published without arguments).
const JobAssociationQuoteAssign = "association_quote_assign"

// QuoteAssignAssociation is one association the task iterates, with its
// owner's e-mail and balance year start (select_related('user')).
type QuoteAssignAssociation struct {
	ID           string  `db:"sport_association_id"`
	OwnerID      string  `db:"user_id"`
	OwnerEmail   string  `db:"owner_email"`
	Denomination *string `db:"denomination"`
	Plans        *string `db:"subscription_fee_plans"`
	StartDay     int     `db:"balance_sheet_start_day"`
	StartMonth   int     `db:"balance_sheet_start_month"`
}

// QuoteAssignSubscription is one balance-year subscription with the user and
// associate fields the task reads.
type QuoteAssignSubscription struct {
	ID             string  `db:"subscription_id"`
	UserID         string  `db:"user_id"`
	UserEmail      string  `db:"user_email"`
	AssociateID    *string `db:"associate_id"`
	AssociateFirst *string `db:"associate_first_name"`
	AssociateLast  *string `db:"associate_last_name"`
}

type QuoteAssignStore interface {
	// BeginQuoteAssign opens a READ COMMITTED transaction fenced by the job
	// lease; each Django autocommit step is one such transaction.
	BeginQuoteAssign(context.Context, Job) (QuoteAssignTransaction, error)
}

type QuoteAssignTransaction interface {
	PaymentWritesTransaction
	// QuoteAssignAssociations is the task's SportAssociation queryset
	// (live, with plans, multiple fees and an advanced plan), read through the
	// same server-side cursor as .iterator(chunk_size=50), unordered.
	QuoteAssignAssociations(context.Context) ([]QuoteAssignAssociation, error)
	// QuoteAssignSubscriptions is the balance-year subscription queryset
	// filtered by payment__meta__icontains, unordered as in Django.
	QuoteAssignSubscriptions(ctx context.Context, association string, from, to time.Time, pattern string) ([]QuoteAssignSubscription, error)
	QuoteExists(ctx context.Context, check PaymentQuoteCheck) (bool, error)
	CompleteJob(ctx context.Context, id, token uuid.UUID) error
}
