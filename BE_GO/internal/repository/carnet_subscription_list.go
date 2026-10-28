package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type CarnetSubscriptionLists struct{ db *sqlx.DB }

func NewCarnetSubscriptionLists(db *sqlx.DB) *CarnetSubscriptionLists {
	return &CarnetSubscriptionLists{db: db}
}

// carnetListScope renders scoped_queryset for an association-scoped session:
// $2 is the scope user for the family CTE, $3 the group, $4 the association.
// The scope_parameters CTE declares their types on every query path.
func carnetListScope(scope *domain.CarnetHoldingScope, subscription, carnet string) (cte, predicate string) {
	cte = `WITH scope_parameters AS (SELECT $4::uuid association_id, $2::uuid effective_id, $3::uuid group_id)`
	if scope == nil {
		return cte, ""
	}
	predicate = " AND " + subscription + ".sport_association_id=$4::uuid"
	if carnet != "" {
		predicate += " AND " + carnet + ".sport_association_id=$4::uuid"
	}
	if scope.Family {
		cte += replaceScopeParameters(familyScopeCTE)
		predicate += " AND " + subscription + ".subscription_id IN (SELECT subscription_id FROM visible_subscriptions)"
	}
	return cte, predicate
}

// familyScopeCTE binds $1 to the association; rebind it to $4 here.
func replaceScopeParameters(query string) string {
	out := []byte{}
	for index := 0; index < len(query); index++ {
		if query[index] == '$' && index+1 < len(query) && query[index+1] == '1' && (index+2 >= len(query) || query[index+2] < '0' || query[index+2] > '9') {
			out = append(out, '$', '4')
			index++
			continue
		}
		out = append(out, query[index])
	}
	return string(out)
}

func scopeAssociation(scope *domain.CarnetHoldingScope) any {
	if scope == nil {
		return nil
	}
	return scope.AssociationID
}

func (r *CarnetSubscriptionLists) AthleteSubscriptionIDs(ctx context.Context, user string, scope *domain.CarnetHoldingScope, group *string) ([]string, error) {
	cte, predicate := carnetListScope(scope, "s", "")
	ids := []string{}
	err := r.db.SelectContext(ctx, &ids, cte+` SELECT s.subscription_id::text FROM application_subscription s
 WHERE s.user_id=$1 AND ($3::uuid IS NULL OR s.group_id=$3::uuid)`+predicate,
		user, user, group, scopeAssociation(scope))
	return ids, err
}

func (r *CarnetSubscriptionLists) ListedSubscription(ctx context.Context, id string, owner *string, scope *domain.CarnetHoldingScope, group *string,
	user string) (*domain.CarnetListedSubscription, error) {
	cte, predicate := carnetListScope(scope, "s", "")
	var row domain.CarnetListedSubscription
	err := r.db.GetContext(ctx, &row, cte+` SELECT s.subscription_id, s.associate_id, s.sport_association_id FROM application_subscription s
 LEFT JOIN application_sportassociation a ON a.sport_association_id=s.sport_association_id
 WHERE s.subscription_id=$1 AND ($3::uuid IS NULL OR s.group_id=$3::uuid) AND ($5::uuid IS NULL OR a.user_id=$5::uuid)`+predicate+` ORDER BY s.subscription_id LIMIT 1`, id, user, group, scopeAssociation(scope), owner)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (r *CarnetSubscriptionLists) ListedHoldings(ctx context.Context, subscription domain.CarnetListedSubscription, scope *domain.CarnetHoldingScope,
	group *string, user string) ([]domain.CarnetListedHolding, []domain.CarnetListedCourse, error) {
	cte, predicate := carnetListScope(scope, "s", "c")
	holdings := []domain.CarnetListedHolding{}
	// Q(subscription=id) | Q(subscription__associate=associate) within the
	// subscription's association; filter(field=None) is IS NULL.
	err := r.db.SelectContext(ctx, &holdings, cte+` SELECT h.carnet_subscription_id, h.carnet_id_id AS carnet_id, c.title, c.fee::text AS fee,
 h.creation_date, s.subscription_id, h.disabled, p.payment_id, p.amount::text AS payment_amount, p.creation_date AS payment_creation_date,
 h.meta::text::bytea AS meta
 FROM application_carnetsubscription h JOIN application_subscription s ON s.subscription_id=h.subscription_id
 LEFT JOIN application_carnet c ON c.carnet_id=h.carnet_id_id LEFT JOIN application_payment p ON p.payment_id=h.payment_id
 WHERE (s.subscription_id=$1 OR s.associate_id IS NOT DISTINCT FROM $5::uuid) AND s.sport_association_id IS NOT DISTINCT FROM $6::uuid`+predicate+`
 ORDER BY h.carnet_subscription_id`, subscription.ID, user, group, scopeAssociation(scope), subscription.AssociateID, subscription.AssociationID)
	if err != nil || len(holdings) == 0 {
		return holdings, nil, err
	}
	ids := make([]string, len(holdings))
	for index, holding := range holdings {
		ids[index] = holding.ID
	}
	courses := []domain.CarnetListedCourse{}
	err = r.db.SelectContext(ctx, &courses, `SELECT l.carnetsubscription_id, c.course_id, c.title AS course_title, cs.course_subscription_id
 FROM application_carnetsubscription_course_subscription l
 JOIN application_coursesubscription cs ON cs.course_subscription_id=l.coursesubscription_id AND NOT cs.deleted
 JOIN application_course c ON c.course_id=cs.course_id
 WHERE l.carnetsubscription_id=ANY($1::uuid[]) ORDER BY l.id`, ids)
	return holdings, courses, err
}
