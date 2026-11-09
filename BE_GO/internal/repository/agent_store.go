package repository

import (
	"context"

	"github.com/jmoiron/sqlx"
)

// AgentStore holds the agent consumer's own queries.
type AgentStore struct{ db *sqlx.DB }

func NewAgentStore(db *sqlx.DB) *AgentStore { return &AgentStore{db: db} }

// AgentAssociation is one SportAssociation row the agent is scoped to.
type AgentAssociation struct {
	ID           string `db:"sport_association_id"`
	Denomination string `db:"denomination"`
}

// Associations is SportAssociation.objects.filter(user=user)[:2] (the
// ActiveManager hides deleted associations); get() needs exactly one.
func (s *AgentStore) Associations(ctx context.Context, user string) ([]AgentAssociation, error) {
	var rows []AgentAssociation
	err := s.db.SelectContext(ctx, &rows, `SELECT sport_association_id::text, denomination FROM application_sportassociation
		WHERE user_id = $1::uuid AND NOT deleted LIMIT 2`, user)
	return rows, err
}

// DeleteSavedReport is SavedReport.objects.filter(saved_report_id=id,
// sport_association_id=association, created_by_id=user).delete().
func (s *AgentStore) DeleteSavedReport(ctx context.Context, id, association, user string) (int64, error) {
	result, err := s.db.ExecContext(ctx, `DELETE FROM application_savedreport WHERE saved_report_id = $1::uuid
		AND sport_association_id = $2::uuid AND created_by_id = $3::uuid`, id, association, user)
	if err != nil {
		return 0, err
	}
	return result.RowsAffected()
}

// AssociationByID is SportAssociation.objects.get(sport_association_id=id).
func (s *AgentStore) AssociationByID(ctx context.Context, id string) (AgentAssociation, bool, error) {
	var rows []AgentAssociation
	err := s.db.SelectContext(ctx, &rows, `SELECT sport_association_id::text, denomination FROM application_sportassociation
		WHERE sport_association_id = $1::uuid AND NOT deleted`, id)
	if err != nil || len(rows) != 1 {
		return AgentAssociation{}, false, err
	}
	return rows[0], true, nil
}
