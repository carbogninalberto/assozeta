package domain

import (
	"context"
	"time"
)

// These projections deliberately contain no credentials or permission JSON.
type ImpersonationAssociation struct {
	ID   string `db:"association_id" json:"id"`
	Name string `db:"association_name" json:"name"`
}

type ImpersonationPerson struct {
	ID              string     `db:"user_id"`
	Username        string     `db:"username"`
	Email           string     `db:"email"`
	FirstName       string     `db:"first_name"`
	LastName        string     `db:"last_name"`
	LastLogin       *time.Time `db:"last_login"`
	Role            int        `db:"role"`
	Deleted         bool       `db:"deleted"`
	AssociationID   *string    `db:"association_id"`
	AssociationName *string    `db:"association_name"`
}

// Filter fields are validated by the service/DTO boundary. The repository still
// uses a fixed ordering whitelist; it never interpolates untrusted SQL.
type ImpersonationListFilter struct {
	Search     string
	Role       int
	Sort       string
	Descending bool
	Limit      int
	Offset     string
}

type ImpersonationTransition struct {
	Actor              Account
	Target             ImpersonationPerson
	SessionID          string
	Action             string
	ScopeAssociationID *string
	Audit              LoginAuditContext
	At                 time.Time
}

type ImpersonationDirectory interface {
	ImpersonationAssociation(context.Context, string) (ImpersonationAssociation, error)
	EligibleImpersonationPerson(context.Context, string, string, string) (ImpersonationPerson, error)
	ListImpersonationPeople(context.Context, string, string, ImpersonationListFilter) ([]ImpersonationPerson, int64, error)
	ImpersonationPersonIncludingDeleted(context.Context, string) (ImpersonationPerson, error)
	AppendImpersonationTransition(context.Context, ImpersonationTransition) error
}

type ImpersonationSessionStore interface {
	Get(context.Context, string) (ImpersonationSession, error)
	Put(context.Context, string, ImpersonationSession) error
	Delete(context.Context, string) error
}

type ImpersonationValidationError struct{ Detail string }

func (e *ImpersonationValidationError) Error() string { return e.Detail }
