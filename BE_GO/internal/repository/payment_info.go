package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// PaymentInfo accepts the caller's DB or transaction. It never opens a hidden
// transaction or derives authorization from the payment owner.
type PaymentInfo struct{ db sqlx.QueryerContext }

func NewPaymentInfo(db sqlx.QueryerContext) *PaymentInfo { return &PaymentInfo{db: db} }

var _ domain.PaymentInfoReader = (*PaymentInfo)(nil)

func (r *PaymentInfo) ReadPaymentInfo(ctx context.Context, lookup domain.PaymentInfoLookup) (*domain.PaymentInfoRecord, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	var row struct {
		ID                                                                                uuid.UUID
		Subject                                                                           int
		Amount                                                                            *string
		CreatedAt                                                                         *time.Time
		Course, Meta                                                                      []byte
		CategoryName                                                                      *string
		UserData, AssociateData, AssociationData, DirectData, InstallmentData, CarnetData []byte
	}
	err := sqlx.GetContext(ctx, r.db, &row, paymentInfoSQL, lookup.PaymentID, lookup.AssociationID, lookup.GroupID)
	if err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		if errors.Is(err, sql.ErrNoRows) {
			return nil, nil
		}
		return nil, err
	}
	record := &domain.PaymentInfoRecord{ID: row.ID, Subject: row.Subject, Amount: row.Amount, CreatedAt: row.CreatedAt,
		Course: json.RawMessage(row.Course), Meta: json.RawMessage(row.Meta), CategoryName: row.CategoryName}
	for _, field := range []struct {
		data        []byte
		destination any
	}{
		{row.UserData, &record.User}, {row.AssociateData, &record.Associate}, {row.AssociationData, &record.Association},
		{row.DirectData, &record.Direct}, {row.InstallmentData, &record.Installment}, {row.CarnetData, &record.Carnet},
	} {
		if len(field.data) != 0 {
			if err := json.Unmarshal(field.data, field.destination); err != nil {
				return nil, err
			}
		}
	}
	return record, nil
}

func (r *PaymentInfo) ReadPaymentInfoCategoryNames(ctx context.Context, ids []uuid.UUID) (map[uuid.UUID]string, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	names := make(map[uuid.UUID]string, len(ids))
	if len(ids) == 0 {
		return names, nil
	}
	var rows []struct {
		ID   uuid.UUID
		Name string
	}
	// pgx's unnamed-statement mode (used through transaction poolers) cannot
	// infer []uuid.UUID for an unknown parameter OID. Text UUIDs retain the
	// validated domain values and PostgreSQL casts the single array explicitly.
	values := make([]string, len(ids))
	for i, id := range ids {
		values[i] = id.String()
	}
	// The characterized lookup uses PaymentCategory.objects, whose manager does
	// not exclude deleted, global or foreign rows. Policy approval is separate.
	if err := sqlx.SelectContext(ctx, r.db, &rows, `SELECT payment_category_id AS id,name FROM application_paymentcategory WHERE payment_category_id=ANY($1::uuid[])`, values); err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, err
	}
	for _, row := range rows {
		names[row.ID] = row.Name
	}
	return names, nil
}

// JSON projections encode nullable related records without float conversion or
// serializer defaults. Stored course/meta JSON is returned directly, unchanged.
// Each lateral query mirrors that relation's manager and scope predicates, then
// selects its first PK. The service, not SQL, chooses among the three candidates.
// FK dereferences intentionally do not inherit related managers' deletion/group
// filters. In particular, an installment can reference a deleted subscription.
const paymentInfoSQL = `SELECT p.payment_id AS id,p.subject,p.amount::text AS amount,p.creation_date AS createdat,
 p.course,p.meta_payment_categories AS meta,category.name AS categoryname,
 CASE WHEN u.user_id IS NOT NULL THEN jsonb_build_object(
  'FirstName',u.first_name,'LastName',u.last_name,'Email',u.email,'Phone',u.phone,'Username',u.username,'Role',u.role) END AS userdata,
 CASE WHEN a.associate_id IS NOT NULL THEN jsonb_build_object(
  'FirstName',a.first_name,'LastName',a.last_name,'TaxCode',a.tax_code) END AS associatedata,
 CASE WHEN association.sport_association_id IS NOT NULL THEN jsonb_build_object(
  'ID',association.sport_association_id,'OwnerID',association.user_id,'Denomination',association.denomination,
  'TaxCode',association.tax_code,'CheckoutInfo',association.checkout_info) END AS associationdata,
 direct.data AS directdata,installment.data AS installmentdata,carnet.data AS carnetdata
FROM application_payment p
LEFT JOIN bakney_user u ON u.user_id=p.user_id
LEFT JOIN application_associate a ON a.associate_id=p.associate_id
LEFT JOIN application_sportassociation association ON association.sport_association_id=p.sport_association_id
LEFT JOIN application_paymentcategory category ON category.payment_category_id=p.payment_category_id
LEFT JOIN LATERAL (
 SELECT jsonb_build_object('ID',s.course_subscription_id,'Offering',jsonb_build_object(
  'ID',c.course_id,'Title',c.title,'Description',c.description,'Fee',c.fee::text,
  'Association',CASE WHEN ca.sport_association_id IS NOT NULL THEN jsonb_build_object(
   'ID',ca.sport_association_id,'OwnerID',ca.user_id,'Denomination',ca.denomination,'TaxCode',ca.tax_code,'CheckoutInfo',ca.checkout_info) END)) AS data
 FROM application_coursesubscription s
 JOIN application_course c ON c.course_id=s.course_id
 LEFT JOIN application_subscription membership ON membership.subscription_id=s.subscription_id
 LEFT JOIN application_sportassociation ca ON ca.sport_association_id=c.sport_association_id
 WHERE p.subject=2 AND s.payment_id=p.payment_id AND NOT s.deleted
  AND ($2::uuid IS NULL OR (membership.sport_association_id=$2 AND c.sport_association_id=$2))
 ORDER BY s.course_subscription_id LIMIT 1
) direct ON TRUE
LEFT JOIN LATERAL (
 SELECT jsonb_build_object('ID',i.course_subscription_installment_id,'Offering',jsonb_build_object(
  'ID',c.course_id,'Title',c.title,'Description',c.description,'Fee',c.fee::text,
  'Association',CASE WHEN ca.sport_association_id IS NOT NULL THEN jsonb_build_object(
   'ID',ca.sport_association_id,'OwnerID',ca.user_id,'Denomination',ca.denomination,'TaxCode',ca.tax_code,'CheckoutInfo',ca.checkout_info) END)) AS data
 FROM application_coursesubscriptioninstallment i
 JOIN application_coursesubscription s ON s.course_subscription_id=i.course_subscription_id
 JOIN application_course c ON c.course_id=s.course_id
 LEFT JOIN application_subscription membership ON membership.subscription_id=s.subscription_id
 LEFT JOIN application_sportassociation ca ON ca.sport_association_id=c.sport_association_id
 WHERE p.subject=2 AND i.payment_id=p.payment_id AND NOT i.deleted
  AND ($3::uuid IS NULL OR i.group_id=$3)
  AND ($2::uuid IS NULL OR (membership.sport_association_id=$2 AND c.sport_association_id=$2))
 ORDER BY i.course_subscription_installment_id LIMIT 1
) installment ON TRUE
LEFT JOIN LATERAL (
 SELECT jsonb_build_object('ID',s.carnet_subscription_id,
  'Offering',CASE WHEN c.carnet_id IS NOT NULL THEN jsonb_build_object(
   'ID',c.carnet_id,'Title',c.title,'Description',c.description,'Fee',c.fee::text,
   'Association',CASE WHEN ca.sport_association_id IS NOT NULL THEN jsonb_build_object(
    'ID',ca.sport_association_id,'OwnerID',ca.user_id,'Denomination',ca.denomination,'TaxCode',ca.tax_code,'CheckoutInfo',ca.checkout_info) END) END) AS data
 FROM application_carnetsubscription s
 LEFT JOIN application_carnet c ON c.carnet_id=s.carnet_id_id
 LEFT JOIN application_subscription membership ON membership.subscription_id=s.subscription_id
 LEFT JOIN application_sportassociation ca ON ca.sport_association_id=c.sport_association_id
 WHERE p.subject=2 AND s.payment_id=p.payment_id
  AND ($2::uuid IS NULL OR (membership.sport_association_id=$2 AND c.sport_association_id=$2))
 ORDER BY s.carnet_subscription_id LIMIT 1
) carnet ON TRUE
WHERE p.payment_id=$1 AND NOT p.deleted
 AND ($2::uuid IS NULL OR p.sport_association_id=$2)
 AND ($3::uuid IS NULL OR p.group_id=$3)`
