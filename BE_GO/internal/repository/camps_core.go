package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var _ domain.CampsCoreStore = (*CampsPeriods)(nil)

func (r *CampsPeriods) BeginCampsCore(ctx context.Context) (domain.CampsCoreTransaction, error) {
	tx, err := r.BeginCamps(ctx)
	if err != nil {
		return nil, err
	}
	return tx.(*campsTransaction), nil
}

const campSelection = `SELECT camps_and_retreats_id, title, description, sport_association_id FROM application_campsandretreats`

func (t *campsTransaction) Camps(ctx context.Context, association string) ([]domain.Camp, error) {
	rows := []domain.Camp{}
	err := t.tx.SelectContext(ctx, &rows, campSelection+` WHERE sport_association_id=$1`, association)
	return rows, err
}

func (t *campsTransaction) Camp(ctx context.Context, id string, lock bool) (*domain.Camp, error) {
	query := campSelection + ` WHERE camps_and_retreats_id=$1`
	if lock {
		query += ` FOR UPDATE`
	}
	var row domain.Camp
	err := t.tx.GetContext(ctx, &row, query, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *campsTransaction) AssociationRelated(ctx context.Context, id string) (domain.CampsRelated, error) {
	return t.related(ctx, `SELECT sport_association_id::text AS association FROM application_sportassociation WHERE sport_association_id=$1`, id)
}

func (t *campsTransaction) AssociationPublic(ctx context.Context, association string) (domain.CampAssociationPublic, error) {
	var row domain.CampAssociationPublic
	err := t.tx.GetContext(ctx, &row, `SELECT a.denomination, u.username FROM application_sportassociation a
 JOIN bakney_user u ON u.user_id=a.user_id WHERE a.sport_association_id=$1`, association)
	return row, err
}

func (t *campsTransaction) InsertCamp(ctx context.Context, row domain.Camp) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_campsandretreats (camps_and_retreats_id, title, description, sport_association_id)
 VALUES ($1,$2,$3,$4)`, row.ID, row.Title, row.Description, row.AssociationID)
	return err
}

func (t *campsTransaction) UpdateCamp(ctx context.Context, row domain.Camp) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_campsandretreats SET title=$2, description=$3, sport_association_id=$4
 WHERE camps_and_retreats_id=$1`, row.ID, row.Title, row.Description, row.AssociationID)
	return err
}

func (t *campsTransaction) CampPeriods(ctx context.Context, camp string) ([]domain.CampsPeriod, error) {
	rows := []domain.CampsPeriod{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT `+campsPeriodSelection+` FROM application_campsandretreatsperiod p
 JOIN application_campsandretreats c ON c.camps_and_retreats_id=p.camps_and_retreats_id WHERE p.camps_and_retreats_id=$1
 ORDER BY p.start_date`, camp)
	return rows, err
}

// DeleteCamp deletes the camp's periods (with their services, subscription
// periods and link rows), the subscription periods left under the camp's
// subscriptions, the subscriptions and the camp.
func (t *campsTransaction) DeleteCamp(ctx context.Context, id string) ([]domain.CampsAudit, error) {
	var periods, subscriptions []string
	if err := t.tx.SelectContext(ctx, &periods, `SELECT camps_and_retreats_period_id::text FROM application_campsandretreatsperiod
 WHERE camps_and_retreats_id=$1 ORDER BY camps_and_retreats_period_id`, id); err != nil {
		return nil, err
	}
	var events []domain.CampsAudit
	for _, period := range periods {
		deleted, err := t.DeletePeriod(ctx, period)
		if err != nil {
			return nil, err
		}
		events = append(events, deleted...)
	}
	if err := t.tx.SelectContext(ctx, &subscriptions, `SELECT camps_and_retreats_subscription_id::text FROM application_campsandretreatssubscription
 WHERE camps_and_retreats_id=$1 ORDER BY camps_and_retreats_subscription_id`, id); err != nil {
		return nil, err
	}
	for _, subscription := range subscriptions {
		var subperiods []string
		if err := t.tx.SelectContext(ctx, &subperiods, `SELECT camps_and_retreats_subscription_period_id::text
 FROM application_campsandretreatssubscriptionperiod WHERE camps_and_retreats_subscription_id=$1
 ORDER BY camps_and_retreats_subscription_period_id`, subscription); err != nil {
			return nil, err
		}
		for _, subperiod := range subperiods {
			if _, err := t.tx.ExecContext(ctx, `DELETE FROM `+campsSubscriptionLinks+` WHERE campsandretreatssubscriptionperiod_id=$1`,
				subperiod); err != nil {
				return nil, err
			}
			changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_campsandretreatssubscriptionperiod",
				"camps_and_retreats_subscription_period_id", subperiod)
			if err != nil {
				return nil, err
			}
			events = append(events, domain.CampsAudit{Model: "CampsAndRetreatsSubscriptionPeriod", Key: subperiod, Action: 2, Changes: changes})
		}
		changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_campsandretreatssubscription", "camps_and_retreats_subscription_id",
			subscription)
		if err != nil {
			return nil, err
		}
		events = append(events, domain.CampsAudit{Model: "CampsAndRetreatsSubscription", Key: subscription, Action: 2, Changes: changes})
	}
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_campsandretreats", "camps_and_retreats_id", id)
	if err != nil {
		return nil, err
	}
	return append(events, domain.CampsAudit{Model: "CampsAndRetreats", Key: id, Action: 2, Changes: changes}), nil
}
