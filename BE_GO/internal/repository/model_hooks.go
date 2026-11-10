package repository

import (
	"context"
	"time"

	"github.com/jmoiron/sqlx"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// modelHooksSubscriptionSeason reads the inputs of the season range that
// Subscription.save() computes for the row (owner reached through the
// association, as self.sport_association.user does).
func modelHooksSubscriptionSeason(ctx context.Context, q sqlx.QueryerContext, subscription string) (domain.SubscriptionSaveSeason, error) {
	var row struct {
		Created                                time.Time `db:"created"`
		StartDay, StartMonth, EndDay, EndMonth int
		CustomEnd                              bool
	}
	err := sqlx.GetContext(ctx, q, &row, `SELECT s.creation_date AS created, u.subscription_start_day AS startday,
 u.subscription_start_month AS startmonth, u.subscription_end_day AS endday, u.subscription_end_month AS endmonth,
 u.custom_end_date AS customend
 FROM application_subscription s
 JOIN application_sportassociation a ON a.sport_association_id=s.sport_association_id
 JOIN bakney_user u ON u.user_id=a.user_id
 WHERE s.subscription_id=$1::uuid`, subscription)
	if err != nil {
		return domain.SubscriptionSaveSeason{}, err
	}
	return domain.SubscriptionSaveSeason{Created: row.Created.UTC(), Rule: domain.CalendarRule{StartDay: row.StartDay,
		StartMonth: row.StartMonth, CustomEnd: row.CustomEnd, EndDay: row.EndDay, EndMonth: row.EndMonth}}, nil
}
