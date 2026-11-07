package repository

import (
	"context"
	"database/sql"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

var _ domain.CampsSubscriptionsStore = (*CampsPeriods)(nil)

func (r *CampsPeriods) BeginCampsSubscriptions(ctx context.Context) (domain.CampsSubscriptionsTransaction, error) {
	tx, err := r.BeginCamps(ctx)
	if err != nil {
		return nil, err
	}
	return tx.(*campsTransaction), nil
}

const campSubscriptionSelection = `SELECT s.camps_and_retreats_subscription_id, s.camps_and_retreats_id,
 c.sport_association_id AS camp_association_id, s.status_flag, s.subscription_id, s.created_at
 FROM application_campsandretreatssubscription s JOIN application_campsandretreats c ON c.camps_and_retreats_id=s.camps_and_retreats_id`

func (t *campsTransaction) CampSubscriptions(ctx context.Context, association, camp string) ([]domain.CampSubscription, error) {
	rows := []domain.CampSubscription{}
	err := t.tx.SelectContext(ctx, &rows, campSubscriptionSelection+` WHERE c.sport_association_id=$1 AND s.camps_and_retreats_id=$2
 ORDER BY s.created_at DESC`, association, camp)
	return rows, err
}

func (t *campsTransaction) CampSubscription(ctx context.Context, id string, lock bool) (*domain.CampSubscription, error) {
	query := campSubscriptionSelection + ` WHERE s.camps_and_retreats_subscription_id=$1`
	if lock {
		query += ` FOR UPDATE OF s`
	}
	var row domain.CampSubscription
	err := t.tx.GetContext(ctx, &row, query, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *campsTransaction) CampSubscriptionIDTaken(ctx context.Context, id string) (bool, error) {
	return t.exists(ctx, `SELECT EXISTS(SELECT 1 FROM application_campsandretreatssubscription WHERE camps_and_retreats_subscription_id=$1)`, id)
}

// RegistrationRelated is Subscription.objects.get(pk=id): the group-aware
// manager filters by X-Group-Id.
func (t *campsTransaction) RegistrationRelated(ctx context.Context, id string, group *string) (*domain.CampRegistration, error) {
	var row domain.CampRegistration
	err := t.tx.GetContext(ctx, &row, `SELECT s.subscription_id, s.sport_association_id, s.user_id, s.associate_id, s.archived, s.deleted,
 s.group_id, a.user_id AS associate_user_id, a.family_id FROM application_subscription s
 LEFT JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE s.subscription_id=$1 AND ($2::uuid IS NULL OR s.group_id=$2::uuid)`, id, group)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *campsTransaction) RegistrationBasic(ctx context.Context, id string, group *string) (*domain.CampRegistrationBasic, error) {
	var row domain.CampRegistrationBasic
	err := t.tx.GetContext(ctx, &row, `SELECT s.subscription_id, s.start_date, s.end_date, s.associate_id, a.first_name, a.last_name,
 a.tax_code, a.family_id, a.family_role FROM application_subscription s LEFT JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE s.subscription_id=$1 AND ($2::uuid IS NULL OR s.group_id=$2::uuid)`, id, group)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *campsTransaction) CampOwnerUser(ctx context.Context, camp string) (string, error) {
	var user string
	err := t.tx.GetContext(ctx, &user, `SELECT a.user_id FROM application_campsandretreats c
 JOIN application_sportassociation a ON a.sport_association_id=c.sport_association_id WHERE c.camps_and_retreats_id=$1`, camp)
	return user, err
}

func (t *campsTransaction) CampTitle(ctx context.Context, camp string) (*string, error) {
	var title *string
	err := t.tx.GetContext(ctx, &title, `SELECT title FROM application_campsandretreats WHERE camps_and_retreats_id=$1`, camp)
	return title, err
}

func (t *campsTransaction) FamilyOwned(ctx context.Context, user, association, family string, group *string) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_subscription s
 JOIN application_associate a ON a.associate_id=s.associate_id
 WHERE s.user_id=$1 AND s.sport_association_id=$2 AND a.family_id=$3 AND NOT s.archived AND NOT s.deleted
 AND ($4::uuid IS NULL OR s.group_id=$4::uuid))`, user, association, family, group)
	return found, err
}

func (t *campsTransaction) PeriodOfCamp(ctx context.Context, period, camp string) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_campsandretreatsperiod
 WHERE camps_and_retreats_period_id=$1 AND camps_and_retreats_id=$2)`, period, camp)
	return found, err
}

func (t *campsTransaction) ServiceOfPeriod(ctx context.Context, service, period string) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(SELECT 1 FROM application_campsandretreatsperiodsservice
 WHERE camps_and_retreats_period_service_id=$1 AND camps_and_retreats_period_id=$2)`, service, period)
	return found, err
}

func (t *campsTransaction) PeriodExists(ctx context.Context, id string) (bool, error) {
	return t.PeriodIDTaken(ctx, id)
}

func (t *campsTransaction) ExistingServices(ctx context.Context, ids []string) ([]string, error) {
	found := []string{}
	if len(ids) == 0 {
		return found, nil
	}
	err := t.tx.SelectContext(ctx, &found, `SELECT camps_and_retreats_period_service_id::text FROM application_campsandretreatsperiodsservice
 WHERE camps_and_retreats_period_service_id = ANY($1::uuid[])`, ids)
	return found, err
}

func (t *campsTransaction) GroupExists(ctx context.Context, id string) (bool, error) {
	return t.exists(ctx, `SELECT EXISTS(SELECT 1 FROM application_group WHERE group_id=$1::uuid)`, id)
}

// DefaultBankAccount is Payment.get_default_bank_account (first() by pk).
func (t *campsTransaction) DefaultBankAccount(ctx context.Context, association string) (*string, error) {
	var id string
	err := t.tx.GetContext(ctx, &id, `SELECT custom_account_id FROM application_customaccounts
 WHERE sport_association_id=$1 AND NOT editable AND enabled AND account_type=2 ORDER BY custom_account_id LIMIT 1`, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &id, nil
}

func (t *campsTransaction) InsertCampSubscription(ctx context.Context, row domain.CampSubscription) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_campsandretreatssubscription (camps_and_retreats_subscription_id,
 camps_and_retreats_id, status_flag, subscription_id, created_at) VALUES ($1,$2,$3,$4,$5)`,
		row.ID, row.CampID, row.Status, row.RegistrationID, row.CreatedAt)
	return err
}

func (t *campsTransaction) InsertSubscriptionPeriod(ctx context.Context, id, period, subscription string) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_campsandretreatssubscriptionperiod (camps_and_retreats_subscription_period_id,
 camps_and_retreats_period_id, camps_and_retreats_subscription_id, payment_id) VALUES ($1,$2,$3,NULL)`, id, period, subscription)
	return err
}

// saveSubscriptionPeriod is Model.save(): every column is written again.
func (t *campsTransaction) saveSubscriptionPeriod(ctx context.Context, id string, payment *string, setPayment bool) error {
	query := `UPDATE application_campsandretreatssubscriptionperiod SET camps_and_retreats_period_id=camps_and_retreats_period_id,
 camps_and_retreats_subscription_id=camps_and_retreats_subscription_id, payment_id=payment_id WHERE camps_and_retreats_subscription_period_id=$1`
	args := []any{id}
	if setPayment {
		query = `UPDATE application_campsandretreatssubscriptionperiod SET camps_and_retreats_period_id=camps_and_retreats_period_id,
 camps_and_retreats_subscription_id=camps_and_retreats_subscription_id, payment_id=$2 WHERE camps_and_retreats_subscription_period_id=$1`
		args = append(args, payment)
	}
	_, err := t.tx.ExecContext(ctx, query, args...)
	return err
}

func (t *campsTransaction) LinkSubscriptionPeriod(ctx context.Context, id string, services []string) error {
	if len(services) > 0 {
		if _, err := t.tx.ExecContext(ctx, `INSERT INTO `+campsSubscriptionLinks+` (campsandretreatssubscriptionperiod_id,
 campsandretreatsperiodsservice_id) SELECT $1, s FROM unnest($2::uuid[]) AS s WHERE NOT EXISTS (SELECT 1 FROM `+campsSubscriptionLinks+`
 WHERE campsandretreatssubscriptionperiod_id=$1 AND campsandretreatsperiodsservice_id=s)`, id, services); err != nil {
			return err
		}
	}
	return t.saveSubscriptionPeriod(ctx, id, nil, false)
}

func (t *campsTransaction) subscriptionPeriods(ctx context.Context, query string, args ...any) ([]domain.CampSubscriptionPeriod, error) {
	rows := []domain.CampSubscriptionPeriod{}
	if err := t.tx.SelectContext(ctx, &rows, query, args...); err != nil {
		return nil, err
	}
	for index := range rows {
		rows[index].Services = []string{}
		if err := t.tx.SelectContext(ctx, &rows[index].Services, `SELECT campsandretreatsperiodsservice_id::text FROM `+campsSubscriptionLinks+`
 WHERE campsandretreatssubscriptionperiod_id=$1`, rows[index].ID); err != nil {
			return nil, err
		}
	}
	return rows, nil
}

func (t *campsTransaction) SubscriptionPeriods(ctx context.Context, subscription string) ([]domain.CampSubscriptionPeriod, error) {
	return t.subscriptionPeriods(ctx, `SELECT camps_and_retreats_subscription_period_id, camps_and_retreats_period_id,
 camps_and_retreats_subscription_id, payment_id FROM application_campsandretreatssubscriptionperiod
 WHERE camps_and_retreats_subscription_id=$1`, subscription)
}

// UnpaidSubscriptionPeriods is the filter of delete_unpaid_periods:
// Q(payment__paid=False) | Q(payment__isnull=True).
func (t *campsTransaction) UnpaidSubscriptionPeriods(ctx context.Context, subscription string) ([]domain.CampSubscriptionPeriod, error) {
	return t.subscriptionPeriods(ctx, `SELECT sp.camps_and_retreats_subscription_period_id, sp.camps_and_retreats_period_id,
 sp.camps_and_retreats_subscription_id, sp.payment_id FROM application_campsandretreatssubscriptionperiod sp
 LEFT OUTER JOIN application_payment p ON p.payment_id=sp.payment_id
 WHERE sp.camps_and_retreats_subscription_id=$1 AND (p.paid=false OR sp.payment_id IS NULL)`, subscription)
}

func (t *campsTransaction) PaidPeriodIDs(ctx context.Context, subscription string) ([]string, error) {
	ids := []string{}
	err := t.tx.SelectContext(ctx, &ids, `SELECT sp.camps_and_retreats_period_id::text FROM application_campsandretreatssubscriptionperiod sp
 INNER JOIN application_payment p ON p.payment_id=sp.payment_id WHERE sp.camps_and_retreats_subscription_id=$1 AND p.paid`, subscription)
	return ids, err
}

// GenerateRows is generate_payment_for_periods' queryset (select_related
// period), in the database's order.
func (t *campsTransaction) GenerateRows(ctx context.Context, subscription string) ([]domain.CampGenerateRow, error) {
	rows := []domain.CampGenerateRow{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT sp.camps_and_retreats_subscription_period_id, per.title, per.description, per.fee::text AS fee
 FROM application_campsandretreatssubscriptionperiod sp LEFT OUTER JOIN application_payment p ON p.payment_id=sp.payment_id
 INNER JOIN application_campsandretreatsperiod per ON per.camps_and_retreats_period_id=sp.camps_and_retreats_period_id
 WHERE sp.camps_and_retreats_subscription_id=$1 AND (p.paid=false OR sp.payment_id IS NULL)`, subscription)
	return rows, err
}

// SelectedServices is period.camps_and_retreats_period_services.all().
func (t *campsTransaction) SelectedServices(ctx context.Context, subscriptionPeriod string) ([]domain.CampSelectedService, error) {
	rows := []domain.CampSelectedService{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT s.camps_and_retreats_period_service_id, s.title, s.fee::text AS fee, s.payment_category_id
 FROM application_campsandretreatsperiodsservice s INNER JOIN `+campsSubscriptionLinks+` l
 ON s.camps_and_retreats_period_service_id=l.campsandretreatsperiodsservice_id WHERE l.campsandretreatssubscriptionperiod_id=$1`,
		subscriptionPeriod)
	return rows, err
}

// PeriodServicesUnordered is CampsAndRetreatsPeriodsService.objects.filter(
// camps_and_retreats_period=period) with each category.
func (t *campsTransaction) PeriodServicesUnordered(ctx context.Context, period string) ([]domain.CampsService, error) {
	var rows []domain.CampsService
	if err := t.tx.SelectContext(ctx, &rows, `SELECT `+campsServiceSelection+campsServiceFrom+`
 WHERE s.camps_and_retreats_period_id=$1`, period); err != nil {
		return nil, err
	}
	for index := range rows {
		if rows[index].CategoryID == nil {
			continue
		}
		var category paymentCategoryRow
		if err := t.tx.GetContext(ctx, &category, `SELECT `+paymentCategorySelection+` FROM application_paymentcategory c
 LEFT JOIN application_vatmanagement v ON v.vat_management_id=c.vat_management_id WHERE c.payment_category_id=$1`,
			*rows[index].CategoryID); err != nil {
			return nil, err
		}
		state := category.state()
		rows[index].Category = &state
	}
	return rows, nil
}

// CreatePayment inserts the payment and returns auditlog's create changes:
// the in-memory float amount and metas replace the stored renderings.
func (t *campsTransaction) CreatePayment(ctx context.Context, row domain.CampPaymentCreate) (map[string][2]string, error) {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_payment (payment_id,type,subject,amount,creation_date,paid,expense,
 description,payment_category_id,meta_payment_categories,user_id,associate_id,sport_association_id,archived,notes,custom_accounts_id,
 deleted,group_id) VALUES ($1,'default',0,$2::numeric,$3,false,false,$4,$5,$6::jsonb,$7,$8,$9,false,$10,$11,false,$12)`,
		row.ID, row.Amount, row.CreatedAt, row.Description, row.CategoryID, row.Metas, row.UserID, row.AssociateID, row.AssociationID, row.Notes,
		row.AccountID, row.GroupID); err != nil {
		return nil, err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_payment", "payment_id", row.ID)
	if err != nil {
		return nil, err
	}
	changes["amount"] = [2]string{"None", row.AmountRepr}
	changes["meta_payment_categories"] = [2]string{"None", row.MetasAudit}
	return changes, nil
}

func (t *campsTransaction) SetSubscriptionPeriodPayment(ctx context.Context, id, payment string) error {
	return t.saveSubscriptionPeriod(ctx, id, &payment, true)
}

func (t *campsTransaction) courseSubscriptions() *courseSubscriptionTransaction {
	return &courseSubscriptionTransaction{paymentCategoryTransaction: t.paymentCategoryTransaction}
}

func (t *campsTransaction) DeletePayment(ctx context.Context, id string, audit domain.CourseSubscriptionAudit) error {
	payments := t.courseSubscriptions()
	snapshot, err := payments.snapshot(ctx, "application_payment", "payment_id", id)
	if err != nil || snapshot == nil {
		return err
	}
	return payments.deletePayments(ctx, []*courseSubscriptionSnapshot{snapshot}, true, audit)
}

func (t *campsTransaction) SubscriptionPeriodChanges(ctx context.Context, id string) (map[string][2]string, error) {
	changes, _, err := storedModelChanges(ctx, t.tx, "application_campsandretreatssubscriptionperiod", "camps_and_retreats_subscription_period_id",
		id, false, true)
	return changes, err
}

func (t *campsTransaction) DeleteSubscriptionPeriod(ctx context.Context, id string, changes map[string][2]string) (domain.CampsAudit, error) {
	if _, err := t.tx.ExecContext(ctx, `DELETE FROM `+campsSubscriptionLinks+` WHERE campsandretreatssubscriptionperiod_id=$1`, id); err != nil {
		return domain.CampsAudit{}, err
	}
	stored, _, err := deleteModelWithAudit(ctx, t.tx, "application_campsandretreatssubscriptionperiod", "camps_and_retreats_subscription_period_id", id)
	if changes == nil {
		changes = stored
	}
	return domain.CampsAudit{Model: "CampsAndRetreatsSubscriptionPeriod", Key: id, Action: 2, Changes: changes}, err
}

// DeleteCampSubscription is the collector delete: the subscription's periods
// (with their link rows), then the subscription.
func (t *campsTransaction) DeleteCampSubscription(ctx context.Context, id string) ([]domain.CampsAudit, error) {
	var periods []string
	if err := t.tx.SelectContext(ctx, &periods, `SELECT camps_and_retreats_subscription_period_id::text
 FROM application_campsandretreatssubscriptionperiod WHERE camps_and_retreats_subscription_id=$1
 ORDER BY camps_and_retreats_subscription_period_id`, id); err != nil {
		return nil, err
	}
	var events []domain.CampsAudit
	for _, period := range periods {
		event, err := t.DeleteSubscriptionPeriod(ctx, period, nil)
		if err != nil {
			return nil, err
		}
		events = append(events, event)
	}
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_campsandretreatssubscription", "camps_and_retreats_subscription_id", id)
	if err != nil {
		return nil, err
	}
	return append(events, domain.CampsAudit{Model: "CampsAndRetreatsSubscription", Key: id, Action: 2, Changes: changes}), nil
}

func (t *campsTransaction) ListPayment(ctx context.Context, id string, group *string) (domain.CourseSubscriptionPayment, error) {
	return t.courseSubscriptions().payment(ctx, id, group)
}

// PaidPeriodsOf is get_paid_periods: the periods of the subscription periods
// whose payment is a paid live group-aware payment of the associate.
func (t *campsTransaction) PaidPeriodsOf(ctx context.Context, associate *string, group *string) ([]string, error) {
	ids := []string{}
	err := t.tx.SelectContext(ctx, &ids, `SELECT sp.camps_and_retreats_period_id::text FROM application_campsandretreatssubscriptionperiod sp
 WHERE sp.payment_id IN (SELECT payment_id FROM application_payment WHERE associate_id IS NOT DISTINCT FROM $1::uuid AND paid
 AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid))`, associate, group)
	return ids, err
}
