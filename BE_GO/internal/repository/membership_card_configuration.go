package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jmoiron/sqlx"
)

type MembershipCardConfigurations struct{ db *sqlx.DB }

func NewMembershipCardConfigurations(db *sqlx.DB) *MembershipCardConfigurations {
	return &MembershipCardConfigurations{db: db}
}

var _ domain.MembershipCardConfigurationStore = (*MembershipCardConfigurations)(nil)

func (r *MembershipCardConfigurations) BeginCardConfiguration(ctx context.Context) (domain.MembershipCardConfigurationTransaction, error) {
	tx, err := r.db.BeginTxx(ctx, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, cardConfigurationError(err)
	}
	return &cardConfigurationTransaction{tx: tx}, nil
}

type cardConfigurationTransaction struct{ tx *sqlx.Tx }

func (t *cardConfigurationTransaction) LockAssociation(ctx context.Context, owner, association string) error {
	var id string
	err := t.tx.GetContext(ctx, &id, `SELECT sport_association_id FROM application_sportassociation
 WHERE sport_association_id=$1 AND user_id=$2 AND NOT deleted FOR UPDATE`, association, owner)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.ErrAssociationNotFound
	}
	return cardConfigurationError(err)
}
func (t *cardConfigurationTransaction) FirstConfiguration(ctx context.Context, association string) (*domain.MembershipCardConfiguration, error) {
	var row domain.MembershipCardConfiguration
	err := t.tx.GetContext(ctx, &row, `SELECT sport_association_membership_card_configuration_id,sport_association_id,
 emit_only_on_approval,COALESCE(customized_template,'null'::jsonb) AS customized_template
 FROM application_sportassociationmembershipcardconfiguration WHERE sport_association_id=$1
 ORDER BY sport_association_membership_card_configuration_id LIMIT 1`, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, cardConfigurationError(err)
	}
	return &row, nil
}
func (t *cardConfigurationTransaction) CreateDefault(ctx context.Context, id, association string) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_sportassociationmembershipcardconfiguration
 (sport_association_membership_card_configuration_id,sport_association_id,emit_only_on_approval,customized_template)
 VALUES($1,$2,false,NULL)`, id, association)
	return cardConfigurationError(err)
}
func (t *cardConfigurationTransaction) AppendConfigurationAudit(ctx context.Context, event domain.MembershipCardConfigurationAudit) error {
	path := "direct"
	return cardConfigurationError(appendModelAudit(ctx, t.tx, modelAuditEntry{
		Model: "sportassociationmembershipcardconfiguration", Key: event.ID,
		Representation: "SportAssociationMembershipCardConfiguration object (" + event.ID + ")", Action: 0,
		Changes: event.Changes, Context: event.Context, AssociationID: &event.AssociationID, ResolutionPath: &path, At: event.At,
	}))
}
func (t *cardConfigurationTransaction) Commit() error   { return cardConfigurationError(t.tx.Commit()) }
func (t *cardConfigurationTransaction) Rollback() error { return t.tx.Rollback() }
func cardConfigurationError(err error) error {
	var pg *pgconn.PgError
	if errors.As(err, &pg) && (pg.Code == "40001" || pg.Code == "40P01") {
		return errors.Join(domain.ErrCardConfigurationConflict, err)
	}
	return err
}
