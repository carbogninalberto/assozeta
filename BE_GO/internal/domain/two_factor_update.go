package domain

import (
	"context"
	"time"
)

type TwoFactorState struct {
	ID        string  `db:"user_id"`
	FirstName string  `db:"first_name"`
	LastName  string  `db:"last_name"`
	Enabled   bool    `db:"two_fa"`
	Secret    *string `db:"two_fa_secret" json:"-"`
}
type TwoFactorAudit struct {
	AccountID, ObjectRepresentation string
	Changes                         map[string][2]string
	Context                         LoginAuditContext
	AssociationID, ResolutionPath   *string
	At                              time.Time
}
type TwoFactorUpdateStore interface {
	BeginTwoFactorUpdate(context.Context) (TwoFactorUpdateTransaction, error)
}
type TwoFactorUpdateTransaction interface {
	TwoFactorForUpdate(context.Context, string) (TwoFactorState, error)
	SetTwoFactor(context.Context, string, bool, *string) error
	AuditAssociations(context.Context, string, *string) (map[string]string, error)
	AppendTwoFactorAudit(context.Context, TwoFactorAudit) error
	Commit() error
	Rollback() error
}
