package domain

import (
	"context"
	"encoding/json"
	"errors"
	"time"
)

var ErrCardConfigurationConflict = errors.New("membership card configuration conflict")

type MembershipCardConfiguration struct {
	ID                 string          `db:"sport_association_membership_card_configuration_id"`
	AssociationID      string          `db:"sport_association_id"`
	EmitOnlyOnApproval bool            `db:"emit_only_on_approval"`
	CustomizedTemplate json.RawMessage `db:"customized_template"`
}

type MembershipCardConfigurationAudit struct {
	ID, AssociationID string
	Changes           map[string][2]string
	Context           LoginAuditContext
	At                time.Time
}

type MembershipCardConfigurationStore interface {
	BeginCardConfiguration(context.Context) (MembershipCardConfigurationTransaction, error)
}

type MembershipCardConfigurationTransaction interface {
	LockAssociation(context.Context, string, string) error
	FirstConfiguration(context.Context, string) (*MembershipCardConfiguration, error)
	CreateDefault(context.Context, string, string) error
	AppendConfigurationAudit(context.Context, MembershipCardConfigurationAudit) error
	Commit() error
	Rollback() error
}
