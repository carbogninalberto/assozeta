package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type CarnetList struct{ db *sqlx.DB }

func NewCarnetList(db *sqlx.DB) *CarnetList { return &CarnetList{db: db} }

var _ domain.CarnetListStore = (*CarnetList)(nil)

func (r *CarnetList) OwnerCarnets(ctx context.Context, owner string, scope *string) ([]domain.CarnetListCarnet, error) {
	association, err := paymentCategoryAssociation(ctx, r.db, owner, scope, false)
	if err != nil {
		return nil, err
	}
	rows := []domain.CarnetListCarnet{}
	err = r.db.SelectContext(ctx, &rows, `SELECT carnet_id, title, description, lessons_number, creation_date, fee::text AS fee,
 public, meta::text AS meta FROM application_carnet WHERE sport_association_id=$1`, association)
	return rows, err
}

// One query: the holdings with their carnet, subscription, associate and the
// live course subscriptions (the M2M manager is the soft-delete default).
const carnetHoldingsSelect = ` SELECT cs.carnet_subscription_id, cs.user_id_id AS user_id, c.carnet_id, c.title AS carnet_title,
 cs.subscription_id, s.start_date, s.end_date, a.associate_id, a.address, a.address_cap, a.address_city, a.born_city,
 a.born_date, a.email, a.sex, a.first_name, a.last_name, a.is_minor, a.sport_association_id, a.tax_code,
 a.user_id AS associate_user_id, asa.user_id AS association_user_id, a.phone, a.family_id, a.family_role,
 cs.creation_date, cs.payment_id, cs.meta::text AS meta,
 COALESCE((SELECT json_agg(json_build_object('course_id', co.course_id, 'course_subscription_id', x.course_subscription_id,
   'course_title', co.title) ORDER BY t.id)
  FROM application_carnetsubscription_course_subscription t
  JOIN application_coursesubscription x ON x.course_subscription_id=t.coursesubscription_id AND NOT x.deleted
  JOIN application_course co ON co.course_id=x.course_id
  WHERE t.carnetsubscription_id=cs.carnet_subscription_id), '[]'::json)::text AS course_subscriptions`

const carnetHoldingsFrom = `
 FROM application_carnetsubscription cs
 LEFT JOIN application_carnet c ON c.carnet_id=cs.carnet_id_id
 LEFT JOIN application_subscription s ON s.subscription_id=cs.subscription_id
 LEFT JOIN application_associate a ON a.associate_id=s.associate_id
 LEFT JOIN application_sportassociation asa ON asa.sport_association_id=a.sport_association_id`

const carnetHoldingsSQL = carnetHoldingsSelect + carnetHoldingsFrom + `
 WHERE (cs.user_id_id=$2 OR a.user_id=$2)`

func (r *CarnetList) Holdings(ctx context.Context, user string, scope *domain.CarnetHoldingScope) ([]domain.CarnetListHolding, error) {
	query := `WITH scope_parameters AS (SELECT $1::uuid association_id, $2::uuid effective_id, $3::uuid group_id)`
	var association, group any
	predicate := ""
	if scope != nil {
		association = scope.AssociationID
		if scope.GroupID != nil {
			group = *scope.GroupID
		}
		predicate = " AND s.sport_association_id=$1 AND c.sport_association_id=$1"
		if scope.Family {
			query += familyScopeCTE
			predicate += " AND s.subscription_id IN (SELECT subscription_id FROM visible_subscriptions)"
		}
	}
	rows := []domain.CarnetListHolding{}
	err := r.db.SelectContext(ctx, &rows, query+carnetHoldingsSQL+predicate+" ORDER BY cs.creation_date DESC", association, user, group)
	return rows, err
}

var _ domain.CarnetInfoStore = (*CarnetList)(nil)

func (r *CarnetList) OwnedCarnet(ctx context.Context, owner, id string, scope *string) (domain.CarnetListCarnet, error) {
	var row domain.CarnetListCarnet
	err := r.db.GetContext(ctx, &row, `SELECT c.carnet_id, c.title, c.description, c.lessons_number, c.creation_date,
 c.fee::text AS fee, c.public, c.meta::text AS meta FROM application_carnet c
 JOIN application_sportassociation sa ON sa.sport_association_id=c.sport_association_id
 WHERE c.carnet_id=$1 AND sa.user_id=$2 AND ($3::uuid IS NULL OR c.sport_association_id=$3::uuid)`, id, owner, scope)
	if errors.Is(err, sql.ErrNoRows) {
		return row, domain.ErrCarnetNotFound
	}
	return row, err
}

// CarnetHoldings also aggregates the live course subscriptions of each
// holding's subscription.
func (r *CarnetList) CarnetHoldings(ctx context.Context, carnet string, scope *string) ([]domain.CarnetInfoHolding, error) {
	rows := []domain.CarnetInfoHolding{}
	err := r.db.SelectContext(ctx, &rows, carnetHoldingsSelect+`,
 COALESCE((SELECT json_agg(json_build_object('course_id', co.course_id, 'title', co.title, 'description', co.description)
   ORDER BY x.course_subscription_id)
  FROM application_coursesubscription x JOIN application_course co ON co.course_id=x.course_id
  WHERE x.subscription_id=cs.subscription_id AND NOT x.deleted AND co.sport_association_id=c.sport_association_id
   AND ($2::uuid IS NULL OR (s.sport_association_id=$2::uuid AND co.sport_association_id=$2::uuid))), '[]'::json)::text AS subscription_courses`+
		carnetHoldingsFrom+`
 WHERE cs.carnet_id_id=$1 AND ($2::uuid IS NULL OR (s.sport_association_id=$2::uuid AND c.sport_association_id=$2::uuid))
 ORDER BY cs.creation_date DESC`, carnet, scope)
	return rows, err
}
