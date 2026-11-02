package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

// The auto-created many-to-many tables carry Django's truncated names.
const (
	campsPeriodLinks       = `"application_campsandretreatsperiod_camps_and_Retreats_perio5fcd"`
	campsSubscriptionLinks = `"application_campsandretreatssubscriptionperiod_camps_and_reef94"`
)

type CampsPeriods struct{ db *sqlx.DB }

func NewCampsPeriods(db *sqlx.DB) *CampsPeriods { return &CampsPeriods{db: db} }

var _ domain.CampsStore = (*CampsPeriods)(nil)

type campsTransaction struct{ *paymentArchiveTransaction }

func (r *CampsPeriods) BeginCamps(ctx context.Context) (domain.CampsTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &campsTransaction{paymentArchiveTransaction: &paymentArchiveTransaction{
		paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}}, nil
}

func (t *campsTransaction) ActorAssociation(ctx context.Context, actor string) (*string, error) {
	var id string
	err := t.tx.GetContext(ctx, &id, `SELECT sport_association_id FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted ORDER BY sport_association_id LIMIT 1`, actor)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &id, nil
}

const campsPeriodSelection = `p.camps_and_retreats_period_id, p.camps_and_retreats_id, c.sport_association_id AS camp_association_id,
 p.start_date, p.end_date, p.title, p.description, p.fee::text AS fee, p.max_participants, p.created_at`

func (t *campsTransaction) Period(ctx context.Context, id string, lock bool) (*domain.CampsPeriod, error) {
	query := `SELECT ` + campsPeriodSelection + ` FROM application_campsandretreatsperiod p
 JOIN application_campsandretreats c ON c.camps_and_retreats_id=p.camps_and_retreats_id WHERE p.camps_and_retreats_period_id=$1`
	if lock {
		query += ` FOR UPDATE OF p`
	}
	var row domain.CampsPeriod
	err := t.tx.GetContext(ctx, &row, query, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *campsTransaction) PeriodServiceIDs(ctx context.Context, period string) ([]string, error) {
	ids := []string{}
	err := t.tx.SelectContext(ctx, &ids, `SELECT campsandretreatsperiodsservice_id::text FROM `+campsPeriodLinks+`
 WHERE campsandretreatsperiod_id=$1 ORDER BY id`, period)
	return ids, err
}

func (t *campsTransaction) exists(ctx context.Context, query, id string) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, query, id)
	return found, err
}

func (t *campsTransaction) PeriodIDTaken(ctx context.Context, id string) (bool, error) {
	return t.exists(ctx, `SELECT EXISTS(SELECT 1 FROM application_campsandretreatsperiod WHERE camps_and_retreats_period_id=$1)`, id)
}

func (t *campsTransaction) ServiceIDTaken(ctx context.Context, id string) (bool, error) {
	return t.exists(ctx, `SELECT EXISTS(SELECT 1 FROM application_campsandretreatsperiodsservice WHERE camps_and_retreats_period_service_id=$1)`, id)
}

func (t *campsTransaction) related(ctx context.Context, query, id string) (domain.CampsRelated, error) {
	var rows []struct {
		Association *string `db:"association"`
	}
	if err := t.tx.SelectContext(ctx, &rows, query, id); err != nil {
		return domain.CampsRelated{}, err
	}
	if len(rows) == 0 {
		return domain.CampsRelated{}, nil
	}
	return domain.CampsRelated{Found: true, AssociationID: rows[0].Association}, nil
}

func (t *campsTransaction) CampRelated(ctx context.Context, id string) (domain.CampsRelated, error) {
	return t.related(ctx, `SELECT sport_association_id::text AS association FROM application_campsandretreats WHERE camps_and_retreats_id=$1`, id)
}

func (t *campsTransaction) PeriodRelated(ctx context.Context, id string) (domain.CampsRelated, error) {
	return t.related(ctx, `SELECT c.sport_association_id::text AS association FROM application_campsandretreatsperiod p
 JOIN application_campsandretreats c ON c.camps_and_retreats_id=p.camps_and_retreats_id WHERE p.camps_and_retreats_period_id=$1`, id)
}

func (t *campsTransaction) ServiceRelated(ctx context.Context, id string) (domain.CampsRelated, error) {
	return t.related(ctx, `SELECT c.sport_association_id::text AS association FROM application_campsandretreatsperiodsservice s
 JOIN application_campsandretreatsperiod p ON p.camps_and_retreats_period_id=s.camps_and_retreats_period_id
 JOIN application_campsandretreats c ON c.camps_and_retreats_id=p.camps_and_retreats_id WHERE s.camps_and_retreats_period_service_id=$1`, id)
}

func (t *campsTransaction) CategoryRelated(ctx context.Context, id string) (domain.CampsRelated, error) {
	return t.related(ctx, `SELECT sport_association_id::text AS association FROM application_paymentcategory WHERE payment_category_id=$1`, id)
}

func (t *campsTransaction) InsertPeriod(ctx context.Context, row domain.CampsPeriod) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_campsandretreatsperiod (camps_and_retreats_period_id, camps_and_retreats_id,
 start_date, end_date, title, description, fee, max_participants, created_at) VALUES ($1,$2,$3,$4,$5,$6,$7::numeric,$8,$9)`,
		row.ID, row.CampID, row.Start, row.End, row.Title, row.Description, row.Fee, row.Max, row.CreatedAt)
	return err
}

func (t *campsTransaction) UpdatePeriod(ctx context.Context, row domain.CampsPeriod) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_campsandretreatsperiod SET camps_and_retreats_id=$2, start_date=$3, end_date=$4,
 title=$5, description=$6, fee=$7::numeric, max_participants=$8, created_at=$9 WHERE camps_and_retreats_period_id=$1`,
		row.ID, row.CampID, row.Start, row.End, row.Title, row.Description, row.Fee, row.Max, row.CreatedAt)
	return err
}

func (t *campsTransaction) LinkPeriodServices(ctx context.Context, period string, remove, add []string) error {
	for _, service := range remove {
		if _, err := t.tx.ExecContext(ctx, `DELETE FROM `+campsPeriodLinks+` WHERE campsandretreatsperiod_id=$1
 AND campsandretreatsperiodsservice_id=$2`, period, service); err != nil {
			return err
		}
	}
	for _, service := range add {
		if _, err := t.tx.ExecContext(ctx, `INSERT INTO `+campsPeriodLinks+` (campsandretreatsperiod_id, campsandretreatsperiodsservice_id)
 VALUES ($1,$2)`, period, service); err != nil {
			return err
		}
	}
	return nil
}

const campsServiceSelection = `s.camps_and_retreats_period_service_id, s.camps_and_retreats_period_id,
 c.sport_association_id AS camp_association_id, s.title, s.description, s.fee::text AS fee, s.payment_category_id, s.created_at`

const campsServiceFrom = ` FROM application_campsandretreatsperiodsservice s
 JOIN application_campsandretreatsperiod p ON p.camps_and_retreats_period_id=s.camps_and_retreats_period_id
 JOIN application_campsandretreats c ON c.camps_and_retreats_id=p.camps_and_retreats_id`

func (t *campsTransaction) PeriodServices(ctx context.Context, period string) ([]domain.CampsService, error) {
	var rows []domain.CampsService
	if err := t.tx.SelectContext(ctx, &rows, `SELECT `+campsServiceSelection+campsServiceFrom+`
 WHERE s.camps_and_retreats_period_id=$1 ORDER BY s.created_at DESC`, period); err != nil {
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

func (t *campsTransaction) Service(ctx context.Context, id string, lock bool) (*domain.CampsService, error) {
	query := `SELECT ` + campsServiceSelection + campsServiceFrom + ` WHERE s.camps_and_retreats_period_service_id=$1`
	if lock {
		query += ` FOR UPDATE OF s`
	}
	var row domain.CampsService
	err := t.tx.GetContext(ctx, &row, query, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *campsTransaction) InsertService(ctx context.Context, row domain.CampsService) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_campsandretreatsperiodsservice (camps_and_retreats_period_service_id,
 camps_and_retreats_period_id, title, description, fee, payment_category_id, created_at) VALUES ($1,$2,$3,$4,$5::numeric,$6,$7)`,
		row.ID, row.PeriodID, row.Title, row.Description, row.Fee, row.CategoryID, row.CreatedAt)
	return err
}

func (t *campsTransaction) UpdateService(ctx context.Context, row domain.CampsService) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_campsandretreatsperiodsservice SET camps_and_retreats_period_id=$2, title=$3,
 description=$4, fee=$5::numeric, payment_category_id=$6, created_at=$7 WHERE camps_and_retreats_period_service_id=$1`,
		row.ID, row.PeriodID, row.Title, row.Description, row.Fee, row.CategoryID, row.CreatedAt)
	return err
}

func (t *campsTransaction) WalkRows(ctx context.Context, period string) ([]domain.CampsWalkRow, error) {
	var rows []struct {
		SubscriptionPeriodID string  `db:"camps_and_retreats_subscription_period_id"`
		PeriodTitle          *string `db:"period_title"`
		PeriodFee            string  `db:"period_fee"`
		CampTitle            *string `db:"camp_title"`
		CampDescription      *string `db:"camp_description"`
		domain.CampsPayment
	}
	err := t.tx.SelectContext(ctx, &rows, `SELECT sp.camps_and_retreats_subscription_period_id, p.title AS period_title,
 p.fee::text AS period_fee, c.title AS camp_title, c.description AS camp_description,
 pay.payment_id, COALESCE(pay.sport_association_id::text, '') AS sport_association_id, pay.sport_association_id IS NULL AS association_null,
 pay.subject, pay.archived, pay.payment_category_id, cat.name AS payment_category_name, pay.description, pay.meta::text AS meta,
 pay.course::text AS course, pay.amount::text AS amount, pay.notes, pay.meta_payment_categories::text AS meta_payment_categories,
 pay.deleted, pay.group_id::text AS group_id
 FROM application_campsandretreatssubscriptionperiod sp
 JOIN application_payment pay ON pay.payment_id=sp.payment_id
 JOIN application_campsandretreatsperiod p ON p.camps_and_retreats_period_id=sp.camps_and_retreats_period_id
 JOIN application_campsandretreatssubscription s ON s.camps_and_retreats_subscription_id=sp.camps_and_retreats_subscription_id
 JOIN application_campsandretreats c ON c.camps_and_retreats_id=s.camps_and_retreats_id
 LEFT JOIN application_paymentcategory cat ON cat.payment_category_id=pay.payment_category_id
 WHERE sp.camps_and_retreats_period_id=$1 AND NOT pay.paid
 ORDER BY sp.camps_and_retreats_subscription_period_id FOR UPDATE OF pay`, period)
	if err != nil {
		return nil, err
	}
	result := make([]domain.CampsWalkRow, 0, len(rows))
	for _, row := range rows {
		result = append(result, domain.CampsWalkRow{SubscriptionPeriodID: row.SubscriptionPeriodID, PeriodTitle: row.PeriodTitle,
			PeriodFee: row.PeriodFee, CampTitle: row.CampTitle, CampDescription: row.CampDescription, Payment: row.CampsPayment})
	}
	return result, nil
}

// SavePayment writes the columns the walk's Payment.save() can change.
func (t *campsTransaction) SavePayment(ctx context.Context, row domain.CampsPayment) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_payment SET payment_category_id=$2, description=$3,
 meta_payment_categories=$4::jsonb, amount=$5::numeric, notes=$6 WHERE payment_id=$1`,
		row.ID, row.CategoryID, row.Description, row.Metas, row.Amount, row.Notes)
	return err
}

func (t *campsTransaction) DeleteService(ctx context.Context, id string) (domain.CampsAudit, error) {
	for _, query := range []string{
		`DELETE FROM ` + campsPeriodLinks + ` WHERE campsandretreatsperiodsservice_id=$1`,
		`DELETE FROM ` + campsSubscriptionLinks + ` WHERE campsandretreatsperiodsservice_id=$1`,
	} {
		if _, err := t.tx.ExecContext(ctx, query, id); err != nil {
			return domain.CampsAudit{}, err
		}
	}
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_campsandretreatsperiodsservice", "camps_and_retreats_period_service_id", id)
	return domain.CampsAudit{Model: "CampsAndRetreatsPeriodsService", Key: id, Action: 2, Changes: changes}, err
}

func (t *campsTransaction) DeletePeriod(ctx context.Context, id string) ([]domain.CampsAudit, error) {
	var services, subscriptions []string
	if err := t.tx.SelectContext(ctx, &services, `SELECT camps_and_retreats_period_service_id::text FROM application_campsandretreatsperiodsservice
 WHERE camps_and_retreats_period_id=$1 ORDER BY camps_and_retreats_period_service_id`, id); err != nil {
		return nil, err
	}
	if err := t.tx.SelectContext(ctx, &subscriptions, `SELECT camps_and_retreats_subscription_period_id::text
 FROM application_campsandretreatssubscriptionperiod WHERE camps_and_retreats_period_id=$1 ORDER BY camps_and_retreats_subscription_period_id`,
		id); err != nil {
		return nil, err
	}
	var events []domain.CampsAudit
	for _, service := range services {
		event, err := t.DeleteService(ctx, service)
		if err != nil {
			return nil, err
		}
		events = append(events, event)
	}
	for _, subscription := range subscriptions {
		if _, err := t.tx.ExecContext(ctx, `DELETE FROM `+campsSubscriptionLinks+` WHERE campsandretreatssubscriptionperiod_id=$1`,
			subscription); err != nil {
			return nil, err
		}
		changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_campsandretreatssubscriptionperiod",
			"camps_and_retreats_subscription_period_id", subscription)
		if err != nil {
			return nil, err
		}
		events = append(events, domain.CampsAudit{Model: "CampsAndRetreatsSubscriptionPeriod", Key: subscription, Action: 2, Changes: changes})
	}
	if _, err := t.tx.ExecContext(ctx, `DELETE FROM `+campsPeriodLinks+` WHERE campsandretreatsperiod_id=$1`, id); err != nil {
		return nil, err
	}
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_campsandretreatsperiod", "camps_and_retreats_period_id", id)
	if err != nil {
		return nil, err
	}
	return append(events, domain.CampsAudit{Model: "CampsAndRetreatsPeriod", Key: id, Action: 2, Changes: changes}), nil
}

func (t *campsTransaction) AppendAudit(ctx context.Context, event domain.CampsAudit, audit domain.LoginAuditContext, at time.Time) error {
	entry := modelAuditEntry{Model: lowerASCII(event.Model), Key: event.Key, Representation: event.Model + " object (" + event.Key + ")",
		Action: event.Action, Changes: event.Changes, Context: audit, At: at}
	if event.AssociationID != nil {
		path := event.Path
		entry.AssociationID, entry.ResolutionPath = event.AssociationID, &path
	}
	return appendModelAudit(ctx, t.tx, entry)
}

func lowerASCII(text string) string {
	out := []byte(text)
	for index, c := range out {
		if c >= 'A' && c <= 'Z' {
			out[index] = c + 'a' - 'A'
		}
	}
	return string(out)
}
