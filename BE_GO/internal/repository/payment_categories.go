package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type PaymentCategories struct{ db *sqlx.DB }

func NewPaymentCategories(db *sqlx.DB) *PaymentCategories { return &PaymentCategories{db: db} }

func (r *PaymentCategories) ListPaymentCategories(ctx context.Context, owner string, impersonationAssociation *string) ([]domain.PaymentCategoryState, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	association, err := paymentCategoryAssociation(ctx, r.db, owner, impersonationAssociation, false)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		var rows []paymentCategoryRow
		if err = sqlx.SelectContext(ctx, r.db, &rows, paymentCategoryGlobalListSQL); err != nil {
			return nil, paymentCategoryTransactionError(err)
		}
		result := make([]domain.PaymentCategoryState, 0, len(rows))
		for _, row := range rows {
			result = append(result, row.state())
		}
		return result, nil
	}
	if err != nil {
		return nil, err
	}
	var rows []paymentCategoryRow
	if err = sqlx.SelectContext(ctx, r.db, &rows, paymentCategoryListSQL, association); err != nil {
		if ctx.Err() != nil {
			return nil, ctx.Err()
		}
		return nil, err
	}
	result := make([]domain.PaymentCategoryState, 0, len(rows))
	for _, row := range rows {
		result = append(result, row.state())
	}
	return result, nil
}

func (r *PaymentCategories) BeginPaymentCategories(ctx context.Context) (domain.PaymentCategoryTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &paymentCategoryTransaction{ownedTransaction: tx}, nil
}

type paymentCategoryTransaction struct{ *ownedTransaction }

func (t *paymentCategoryTransaction) OwnerForUpdate(ctx context.Context, id string) (int, bool, bool, error) {
	var owner struct {
		Role    int  `db:"role"`
		Active  bool `db:"is_active"`
		Deleted bool `db:"deleted"`
	}
	err := t.tx.GetContext(ctx, &owner, `SELECT role,is_active,deleted FROM bakney_user WHERE user_id=$1 FOR UPDATE`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return 0, false, false, domain.ErrPaymentCategoryForbidden
	}
	return owner.Role, owner.Active, owner.Deleted, paymentCategoryTransactionError(err)
}

func (t *paymentCategoryTransaction) AssociationForOwner(ctx context.Context, owner string, impersonationAssociation *string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, impersonationAssociation, true)
}

func (t *paymentCategoryTransaction) AssociationForActor(ctx context.Context, actor string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, actor, nil, false)
}

func (t *paymentCategoryTransaction) AssociationExists(ctx context.Context, id string) (bool, error) {
	var exists bool
	err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_sportassociation WHERE sport_association_id=$1)`, id)
	return exists, paymentCategoryTransactionError(err)
}

func (t *paymentCategoryTransaction) PaymentCategoryIDExists(ctx context.Context, id string) (bool, error) {
	var exists bool
	err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_paymentcategory WHERE payment_category_id=$1)`, id)
	return exists, paymentCategoryTransactionError(err)
}

func (t *paymentCategoryTransaction) CategoryForUpdate(ctx context.Context, id, association string) (domain.PaymentCategoryState, error) {
	var row paymentCategoryRow
	err := sqlx.GetContext(ctx, t.tx, &row, paymentCategoryLockSQL, id, association)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.PaymentCategoryState{}, domain.ErrPaymentCategoryNotFound
	}
	if err != nil {
		return domain.PaymentCategoryState{}, paymentCategoryTransactionError(err)
	}
	return row.state(), nil
}

func (t *paymentCategoryTransaction) VATByID(ctx context.Context, id string) (*domain.PaymentCategoryVAT, error) {
	var row paymentCategoryVATRow
	err := sqlx.GetContext(ctx, t.tx, &row, `SELECT vat_management_id,vat,deduction FROM application_vatmanagement WHERE vat_management_id=$1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, paymentCategoryTransactionError(err)
	}
	value := row.value()
	return &value, nil
}

func (t *paymentCategoryTransaction) CreateVAT(ctx context.Context, value domain.PaymentCategoryVAT) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_vatmanagement(vat_management_id,vat,deduction) VALUES($1,$2,$3)`,
		value.ID, value.VAT, value.Deduction)
	return paymentCategoryTransactionError(err)
}

func (t *paymentCategoryTransaction) InsertPaymentCategory(ctx context.Context, value domain.PaymentCategoryState) error {
	var vat *string
	if value.VAT != nil {
		vat = &value.VAT.ID
	}
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_paymentcategory
 (payment_category_id,name,creation_date,sport_association_id,expense,tax_deductible,archived,type,vat_management_id,deleted)
 VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)`,
		value.ID, value.Name, value.CreationDate, value.AssociationID, value.Expense, value.TaxDeductible,
		value.Archived, value.Type, vat, value.Deleted)
	return paymentCategoryTransactionError(err)
}

func (t *paymentCategoryTransaction) UpdatePaymentCategory(ctx context.Context, value domain.PaymentCategoryState) error {
	var vat *string
	if value.VAT != nil {
		vat = &value.VAT.ID
	}
	result, err := t.tx.ExecContext(ctx, `UPDATE application_paymentcategory
 SET name=$3,creation_date=$4,expense=$5,tax_deductible=$6,archived=$7,type=$8,vat_management_id=$9,deleted=$10
 WHERE payment_category_id=$1 AND sport_association_id=$2`,
		value.ID, value.AssociationID, value.Name, value.CreationDate, value.Expense,
		value.TaxDeductible, value.Archived, value.Type, vat, value.Deleted)
	if err != nil {
		return paymentCategoryTransactionError(err)
	}
	count, err := result.RowsAffected()
	if err == nil && count != 1 {
		return domain.ErrPaymentCategoryNotFound
	}
	return paymentCategoryTransactionError(err)
}

func (t *paymentCategoryTransaction) AppendPaymentCategoryAudit(ctx context.Context, event domain.PaymentCategoryAudit) error {
	if event.Model != "paymentcategory" && event.Model != "vatmanagement" {
		return domain.ErrInvalidScopedRequest
	}
	return paymentCategoryTransactionError(appendModelAudit(ctx, t.tx, modelAuditEntry{
		Model: event.Model, Key: event.ID, Representation: event.ObjectRepresentation, Action: event.Action,
		Changes: event.Changes, Context: event.Context, AssociationID: event.AssociationID,
		ResolutionPath: event.ResolutionPath, At: event.At,
	}))
}

func paymentCategoryAssociation(ctx context.Context, db sqlx.QueryerContext, owner string, impersonationAssociation *string, lock bool) (string, error) {
	query := `SELECT sport_association_id FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR sport_association_id=$2::uuid)`
	if lock {
		query += ` FOR UPDATE`
	}
	var id string
	err := sqlx.GetContext(ctx, db, &id, query, owner, impersonationAssociation)
	if errors.Is(err, sql.ErrNoRows) {
		return "", domain.ErrAssociationNotFound
	}
	if err != nil {
		return "", paymentCategoryTransactionError(err)
	}
	return id, nil
}

func paymentCategoryTransactionError(err error) error {
	if err == nil {
		return nil
	}
	if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return err
	}
	return err
}

type paymentCategoryVATRow struct {
	ID        string `db:"vat_management_id"`
	VAT       uint16 `db:"vat"`
	Deduction uint16 `db:"deduction"`
}

func (r paymentCategoryVATRow) value() domain.PaymentCategoryVAT {
	return domain.PaymentCategoryVAT{ID: r.ID, VAT: r.VAT, Deduction: r.Deduction}
}

type paymentCategoryRow struct {
	ID            string    `db:"payment_category_id"`
	Name          string    `db:"name"`
	CreationDate  time.Time `db:"creation_date"`
	AssociationID *string   `db:"sport_association_id"`
	Expense       bool      `db:"expense"`
	TaxDeductible bool      `db:"tax_deductible"`
	Archived      bool      `db:"archived"`
	Type          uint16    `db:"type"`
	Deleted       bool      `db:"deleted"`
	VATID         *string   `db:"vat_management_id"`
	VAT           *uint16   `db:"vat"`
	Deduction     *uint16   `db:"deduction"`
}

func (r paymentCategoryRow) state() domain.PaymentCategoryState {
	result := domain.PaymentCategoryState{
		ID: r.ID, Name: r.Name, CreationDate: r.CreationDate, AssociationID: r.AssociationID,
		Expense: r.Expense, TaxDeductible: r.TaxDeductible, Archived: r.Archived,
		Type: r.Type, Deleted: r.Deleted,
	}
	if r.VATID != nil && r.VAT != nil && r.Deduction != nil {
		result.VAT = &domain.PaymentCategoryVAT{ID: *r.VATID, VAT: *r.VAT, Deduction: *r.Deduction}
	}
	return result
}

const paymentCategorySelection = `c.payment_category_id,c.name,c.creation_date,c.sport_association_id,
 c.expense,c.tax_deductible,c.archived,c.type,c.deleted,
 v.vat_management_id,v.vat,v.deduction`

const paymentCategoryListSQL = `SELECT ` + paymentCategorySelection + `
 FROM application_paymentcategory c
 LEFT JOIN application_vatmanagement v ON v.vat_management_id=c.vat_management_id
 WHERE c.sport_association_id=$1 OR c.sport_association_id IS NULL
 ORDER BY c.creation_date DESC`

const paymentCategoryGlobalListSQL = `SELECT ` + paymentCategorySelection + `
 FROM application_paymentcategory c
 LEFT JOIN application_vatmanagement v ON v.vat_management_id=c.vat_management_id
 WHERE c.sport_association_id IS NULL
 ORDER BY c.creation_date DESC`

const paymentCategoryLockSQL = `SELECT ` + paymentCategorySelection + `
 FROM application_paymentcategory c
 LEFT JOIN application_vatmanagement v ON v.vat_management_id=c.vat_management_id
 WHERE c.payment_category_id=$1 AND c.sport_association_id=$2
 FOR UPDATE OF c`
