package repository

import (
	"context"
	"database/sql"
	"strconv"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type Inconsistencies struct{ db *sqlx.DB }

func NewInconsistencies(db *sqlx.DB) *Inconsistencies { return &Inconsistencies{db: db} }

var _ domain.InconsistencyStore = (*Inconsistencies)(nil)

func (r *Inconsistencies) BeginInconsistencies(ctx context.Context) (domain.InconsistencyTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &inconsistencyTransaction{dashboardLayoutTransaction: &dashboardLayoutTransaction{
		paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}}, nil
}

type inconsistencyTransaction struct{ *dashboardLayoutTransaction }

var _ domain.InconsistencyTransaction = (*inconsistencyTransaction)(nil)

// One query: the associates plus each one's primary-tutor state and the
// relation get_main_tutor would promote.
func (t *inconsistencyTransaction) Associates(ctx context.Context, user string, group, scope *string) ([]domain.InconsistencyAssociate, error) {
	rows := []domain.InconsistencyAssociate{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT a.associate_id, a.address, a.address_cap, a.address_city, a.born_city,
 a.born_date, a.email, a.sex, a.first_name, a.last_name, a.is_minor, a.sport_association_id, a.tax_code, a.user_id,
 sa.user_id AS association_user_id, a.phone, a.family_id, a.family_role,
 EXISTS(SELECT 1 FROM application_associatetutorrelation r WHERE r.associate_id=a.associate_id AND r.is_primary) AS has_primary,
 (SELECT min(r.id) FROM application_associatetutorrelation r WHERE r.associate_id=a.associate_id) AS first_relation_id
 FROM application_associate a LEFT JOIN application_sportassociation sa ON sa.sport_association_id=a.sport_association_id
 WHERE a.user_id=$1 AND NOT a.deleted AND ($2::uuid IS NULL OR a.group_id=$2::uuid)
 AND ($3::uuid IS NULL OR a.sport_association_id=$3::uuid)`, user, group, scope)
	return rows, err
}

func (t *inconsistencyTransaction) PromoteTutorRelation(ctx context.Context, relation int64) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_associatetutorrelation SET is_primary=true WHERE id=$1`, relation)
	return err
}

func (t *inconsistencyTransaction) AppendTutorPromotionAudit(ctx context.Context, event domain.TutorPromotionAudit) error {
	key := strconv.FormatInt(event.RelationID, 10)
	return appendModelAudit(ctx, t.tx, modelAuditEntry{Model: "associatetutorrelation", Key: key,
		Representation: "AssociateTutorRelation object (" + key + ")", Action: 1,
		Changes: map[string][2]string{"is_primary": {"False", "True"}}, Context: event.Context,
		AssociationID: event.AssociationID, ResolutionPath: event.Path, At: event.At})
}
