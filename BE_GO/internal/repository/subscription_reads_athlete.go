package repository

import (
	"context"
	"database/sql"
	"errors"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var _ domain.SubscriptionReadsAthleteStore = (*SubscriptionReads)(nil)

func (r *SubscriptionReads) SearchAssociation(ctx context.Context, association string) (*domain.SubscriptionReadsSearchAssociation, error) {
	var row domain.SubscriptionReadsSearchAssociation
	err := r.db.GetContext(ctx, &row, `SELECT a.sport_association_id, a.denomination, a.tax_code, a.address, a.address_cap, a.address_city,
 u.role AS owner_role, u.first_name AS owner_first_name, u.last_name AS owner_last_name, u.username AS owner_username,
 u.avatar_image AS owner_avatar_image, u.disable_account_creation AS owner_disable_account_creation,
 u.force_account_creation AS owner_force_account_creation, u.online_payments AS owner_online_payments,
 (SELECT COUNT(*) FROM application_sportassociation o WHERE o.user_id=u.user_id AND NOT o.deleted) AS owner_associations
 FROM application_sportassociation a JOIN bakney_user u ON u.user_id=a.user_id WHERE a.sport_association_id=$1`, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	return &row, err
}

func (r *SubscriptionReads) AssociationOwner(ctx context.Context, association string) (*string, error) {
	var owner *string
	err := r.db.GetContext(ctx, &owner, `SELECT (SELECT user_id::text FROM application_sportassociation WHERE sport_association_id=$1)`, association)
	return owner, err
}

func (r *SubscriptionReads) AthleteCourses(ctx context.Context, subscriptions []string) ([]domain.SubscriptionReadsAthleteCourse, error) {
	rows := []domain.SubscriptionReadsAthleteCourse{}
	if len(subscriptions) == 0 {
		return rows, nil
	}
	err := r.db.SelectContext(ctx, &rows, `SELECT cs.subscription_id, cs.course_subscription_id, cs.paid, cs.creation_date, cs.one_fee_payment,
 c.course_id, c.title AS course_title, c.one_fee_payment AS course_one_fee_payment, c.one_fee::text AS course_one_fee
 FROM application_coursesubscription cs JOIN application_course c ON c.course_id=cs.course_id
 WHERE NOT cs.deleted AND cs.subscription_id = ANY(string_to_array($1, ',')::uuid[]) ORDER BY cs.ctid`, strings.Join(subscriptions, ","))
	return rows, err
}

func (r *SubscriptionReads) Carnets(ctx context.Context, subscriptions []string) (map[string]bool, error) {
	found := []string{}
	result := map[string]bool{}
	if len(subscriptions) == 0 {
		return result, nil
	}
	if err := r.db.SelectContext(ctx, &found, `SELECT DISTINCT subscription_id::text FROM application_carnetsubscription
 WHERE subscription_id = ANY(string_to_array($1, ',')::uuid[])`, strings.Join(subscriptions, ",")); err != nil {
		return nil, err
	}
	for _, id := range found {
		result[id] = true
	}
	return result, nil
}
