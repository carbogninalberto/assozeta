package repository

import (
	"context"
	"encoding/json"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// Accept a DB or the caller's transaction; never open a hidden transaction.
type PaymentSaveInputs struct{ db sqlx.QueryerContext }

func NewPaymentSaveInputs(db sqlx.QueryerContext) *PaymentSaveInputs {
	return &PaymentSaveInputs{db: db}
}

func (r *PaymentSaveInputs) ReadPaymentSaveInputs(ctx context.Context, refs []domain.PaymentSaveReference, defaultCategory string) ([]domain.PaymentSaveState, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	unique, err := domain.UniquePaymentSaveReferences(refs)
	if err != nil {
		return nil, err
	}
	if len(unique) == 0 {
		return nil, nil
	}
	type requested struct {
		PaymentID     uuid.UUID `json:"payment_id"`
		AssociationID uuid.UUID `json:"association_id"`
		Ordinal       int       `json:"ordinal"`
	}
	requestedRows := make([]requested, len(unique))
	for i, ref := range unique {
		requestedRows[i] = requested{ref.PaymentID, ref.AssociationID, i}
	}
	payload, err := json.Marshal(requestedRows)
	if err != nil {
		return nil, err
	}
	var rows []struct {
		ID, AssociationID                                                    uuid.UUID
		Archived, Allowed                                                    bool
		Subject                                                              int
		OriginalCategoryID, CategoryID, InvoiceID                            uuid.NullUUID
		Description, CategoryName                                            *string
		Meta, Course                                                         []byte
		InvoiceArchived, HasCourse, HasInstallment, HasCarnet, MissingCarnet bool
		CourseTitle, InstallmentTitle, CarnetTitle                           *string
	}
	if err = sqlx.SelectContext(ctx, r.db, &rows, paymentSaveInputsSQL, payload, defaultCategory); err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, err
	}
	if len(rows) != len(unique) {
		return nil, domain.ErrPaymentSaveScope
	}
	optionalID := func(value uuid.NullUUID) *uuid.UUID {
		if !value.Valid {
			return nil
		}
		id := value.UUID
		return &id
	}
	result := make([]domain.PaymentSaveState, 0, len(rows))
	for _, row := range rows {
		if !row.Allowed {
			return nil, domain.ErrPaymentSaveScope
		}
		state := domain.PaymentSaveState{ID: row.ID, AssociationID: row.AssociationID, Archived: row.Archived, Subject: row.Subject,
			OriginalCategoryID: optionalID(row.OriginalCategoryID), CategoryID: optionalID(row.CategoryID), Description: row.Description,
			CategoryName: row.CategoryName, Meta: row.Meta, Course: row.Course}
		if row.InvoiceID.Valid {
			state.Invoice = &domain.PaymentSaveInvoice{ID: row.InvoiceID.UUID, Archived: row.InvoiceArchived}
		}
		if row.HasCourse {
			state.Related[0] = &domain.PaymentSaveRelation{Title: row.CourseTitle}
		}
		if row.HasInstallment {
			state.Related[1] = &domain.PaymentSaveRelation{Title: row.InstallmentTitle}
		}
		if row.HasCarnet {
			state.Related[2] = &domain.PaymentSaveRelation{Title: row.CarnetTitle, MissingCarnet: row.MissingCarnet}
		}
		result = append(result, state)
	}
	return result, nil
}

// One statement provides a consistent snapshot for all requested payments and
// their selected relationships. Existing deleted/archived rows remain visible.
// Each lateral query selects at most one row by PK; lower-priority reverse
// relationships are not read when the model hook would never reach them.
const paymentSaveInputsSQL = `WITH requested AS (
 SELECT * FROM jsonb_to_recordset($1::jsonb) AS r(payment_id uuid,association_id uuid,ordinal integer)
)
SELECT p.payment_id AS id,p.sport_association_id AS associationid,p.archived,p.subject,p.description,p.meta,p.course,
 p.payment_category_id AS originalcategoryid,COALESCE(category.payment_category_id,default_category.payment_category_id) AS categoryid,
 COALESCE(category.name,default_category.name) AS categoryname,
 invoice.invoice_id AS invoiceid,COALESCE(invoice.archived,FALSE) AS invoicearchived,
 cs.id IS NOT NULL AS hascourse,cs.title AS coursetitle,
 installment.id IS NOT NULL AS hasinstallment,installment.title AS installmenttitle,
 carnet.id IS NOT NULL AS hascarnet,carnet.title AS carnettitle,COALESCE(carnet.missing,FALSE) AS missingcarnet,
 COALESCE(
  (p.payment_category_id IS NULL OR category.payment_category_id IS NOT NULL)
  AND (p.invoice_id IS NULL OR invoice.invoice_id IS NOT NULL)
  AND (p.custom_accounts_id IS NULL OR account.custom_account_id IS NOT NULL)
  AND (cs.id IS NULL OR cs.allowed)
  AND (installment.id IS NULL OR installment.allowed)
  AND (carnet.id IS NULL OR carnet.allowed),FALSE) AS allowed
FROM requested r JOIN application_payment p ON p.payment_id=r.payment_id AND p.sport_association_id=r.association_id
LEFT JOIN application_paymentcategory category ON category.payment_category_id=p.payment_category_id
 AND (category.sport_association_id IS NULL OR category.sport_association_id=p.sport_association_id)
LEFT JOIN LATERAL (
 SELECT c.payment_category_id,c.name FROM application_paymentcategory c
 WHERE p.payment_category_id IS NULL AND UPPER(c.name::text)=UPPER($2)
 AND (c.sport_association_id IS NULL OR c.sport_association_id=p.sport_association_id)
 ORDER BY c.payment_category_id LIMIT 1
) default_category ON TRUE
LEFT JOIN application_invoice invoice ON invoice.invoice_id=p.invoice_id AND invoice.sport_association_id=p.sport_association_id
LEFT JOIN application_customaccounts account ON account.custom_account_id=p.custom_accounts_id AND account.sport_association_id=p.sport_association_id
LEFT JOIN LATERAL (
 SELECT s.course_subscription_id AS id,c.title,
 c.course_id IS NOT NULL AND (s.subscription_id IS NULL OR membership.subscription_id IS NOT NULL) AS allowed
 FROM application_coursesubscription s LEFT JOIN application_course c ON c.course_id=s.course_id AND c.sport_association_id=p.sport_association_id
 LEFT JOIN application_subscription membership ON membership.subscription_id=s.subscription_id AND membership.sport_association_id=p.sport_association_id
 WHERE p.subject=2 AND p.description IS NULL AND s.payment_id=p.payment_id
 ORDER BY s.course_subscription_id LIMIT 1
) cs ON TRUE
LEFT JOIN LATERAL (
 SELECT i.course_subscription_installment_id AS id,c.title,
 c.course_id IS NOT NULL AND (s.subscription_id IS NULL OR membership.subscription_id IS NOT NULL) AS allowed
 FROM application_coursesubscriptioninstallment i JOIN application_coursesubscription s ON s.course_subscription_id=i.course_subscription_id
 LEFT JOIN application_course c ON c.course_id=s.course_id AND c.sport_association_id=p.sport_association_id
 LEFT JOIN application_subscription membership ON membership.subscription_id=s.subscription_id AND membership.sport_association_id=p.sport_association_id
 WHERE p.subject=2 AND p.description IS NULL AND cs.id IS NULL AND i.payment_id=p.payment_id
 ORDER BY i.course_subscription_installment_id LIMIT 1
) installment ON TRUE
LEFT JOIN LATERAL (
 SELECT s.carnet_subscription_id AS id,c.title,s.carnet_id_id IS NULL AS missing,
 (s.carnet_id_id IS NULL OR c.carnet_id IS NOT NULL)
 AND (s.subscription_id IS NULL OR membership.subscription_id IS NOT NULL) AS allowed
 FROM application_carnetsubscription s LEFT JOIN application_carnet c ON c.carnet_id=s.carnet_id_id AND c.sport_association_id=p.sport_association_id
 LEFT JOIN application_subscription membership ON membership.subscription_id=s.subscription_id AND membership.sport_association_id=p.sport_association_id
 WHERE p.subject=2 AND p.description IS NULL AND cs.id IS NULL AND installment.id IS NULL AND s.payment_id=p.payment_id
 ORDER BY s.carnet_subscription_id LIMIT 1
) carnet ON TRUE
ORDER BY r.ordinal`
