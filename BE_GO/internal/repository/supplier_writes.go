package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type SupplierWrites struct{ db *sqlx.DB }

func NewSupplierWrites(db *sqlx.DB) *SupplierWrites { return &SupplierWrites{db: db} }

type supplierWriteTransaction struct{ *paymentCategoryTransaction }

func (r *SupplierWrites) BeginSupplierDelete(ctx context.Context) (domain.SupplierDeleteTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &supplierWriteTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

func (t *supplierWriteTransaction) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, nil, false)
}

func (t *supplierWriteTransaction) LiveSuppliers(ctx context.Context, association string, ids []string, group *string) ([]string, error) {
	found := []string{}
	err := t.tx.SelectContext(ctx, &found, `SELECT supplier_id FROM application_supplierandcustomers
 WHERE supplier_id=ANY($1::uuid[]) AND sport_association_id=$2 AND NOT deleted AND ($3::uuid IS NULL OR group_id=$3::uuid)
 ORDER BY supplier_id FOR UPDATE`, ids, association, group)
	return found, err
}

// supplierReferences are the Django foreign keys to SupplierAndCustomers, all
// on_delete=SET_NULL. Any other database reference refuses the delete rather
// than guessing its Django semantics.
var supplierReferences = map[string]bool{
	"application_payment.supplier_id": true, "application_invoicesuppliers.supplier_id": true,
	"application_subscription.supplier_id": true, "application_soldproducts.supplier_id": true,
}

var errSupplierReferenceUnknown = errors.New("unported supplier reference")

func (t *supplierWriteTransaction) DeleteSupplier(ctx context.Context, id string, audit domain.LoginAuditContext, at time.Time) error {
	refs, err := modelReferences(ctx, t.tx, "application_supplierandcustomers", "supplier_id")
	if err != nil {
		return err
	}
	for _, ref := range refs {
		if !supplierReferences[ref.Table+"."+ref.Column] {
			return errSupplierReferenceUnknown
		}
		if _, err = t.tx.ExecContext(ctx, `UPDATE `+pq(ref.Table)+` SET `+pq(ref.Column)+`=NULL WHERE `+pq(ref.Column)+`=$1`, id); err != nil {
			return err
		}
	}
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_supplierandcustomers", "supplier_id", id)
	if err != nil {
		return err
	}
	entry := modelAuditEntry{Model: "supplierandcustomers", Key: id, Representation: "SupplierAndCustomers object (" + id + ")", Action: 2,
		Changes: changes, Context: audit, At: at}
	if association := changes["sport_association"][0]; association != "" && association != "None" {
		path := "changes_json.sport_association"
		entry.AssociationID, entry.ResolutionPath = &association, &path
	}
	return appendModelAudit(ctx, t.tx, entry)
}
