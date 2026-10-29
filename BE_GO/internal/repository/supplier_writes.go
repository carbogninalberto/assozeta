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

type supplierWriteTransaction struct{ *dashboardLayoutTransaction }

func (r *SupplierWrites) begin(ctx context.Context) (*supplierWriteTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &supplierWriteTransaction{dashboardLayoutTransaction: &dashboardLayoutTransaction{
		paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}}, nil
}

func (r *SupplierWrites) BeginSupplierDelete(ctx context.Context) (domain.SupplierDeleteTransaction, error) {
	return r.begin(ctx)
}

func (r *SupplierWrites) BeginSupplierWrite(ctx context.Context) (domain.SupplierWriteTransaction, error) {
	return r.begin(ctx)
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

func (t *supplierWriteTransaction) AssociationExists(ctx context.Context, id string) (bool, error) {
	var exists bool
	err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM application_sportassociation WHERE sport_association_id=$1)`, id)
	return exists, err
}

func (t *supplierWriteTransaction) GroupAssociation(ctx context.Context, id string) (*string, bool, error) {
	var association *string
	err := t.tx.GetContext(ctx, &association, `SELECT sport_association_id FROM application_group WHERE group_id=$1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, false, nil
	}
	return association, err == nil, err
}

func (t *supplierWriteTransaction) SupplierIDTaken(ctx context.Context, id string, group *string, exclude *string) (bool, error) {
	var taken bool
	err := t.tx.GetContext(ctx, &taken, `SELECT EXISTS(SELECT 1 FROM application_supplierandcustomers
 WHERE supplier_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid) AND ($3::uuid IS NULL OR supplier_id<>$3::uuid))`, id, group, exclude)
	return taken, err
}

func (t *supplierWriteTransaction) LiveSupplier(ctx context.Context, id string, group *string) (*domain.Supplier, error) {
	var row domain.Supplier
	err := t.tx.GetContext(ctx, &row, `SELECT `+supplierColumns+` FROM application_supplierandcustomers
 WHERE supplier_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid) ORDER BY supplier_id LIMIT 1 FOR UPDATE`, id, group)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *supplierWriteTransaction) InsertSupplier(ctx context.Context, row domain.Supplier) error {
	_, err := t.tx.NamedExecContext(ctx, `INSERT INTO application_supplierandcustomers (`+supplierColumns+`)
 VALUES (:supplier_id,:name,:address,:tax_code,:sport_association_id,:vat_number,:type,:email,:phone_number,:city,:cap,:state,
 :country,:nationality,:note,:group_id,:deleted)`, row)
	return err
}

func (t *supplierWriteTransaction) SaveSupplier(ctx context.Context, row domain.Supplier) error {
	_, err := t.tx.NamedExecContext(ctx, `UPDATE application_supplierandcustomers SET name=:name, address=:address, tax_code=:tax_code,
 sport_association_id=:sport_association_id, vat_number=:vat_number, type=:type, email=:email, phone_number=:phone_number, city=:city,
 cap=:cap, state=:state, country=:country, nationality=:nationality, note=:note, group_id=:group_id, deleted=:deleted
 WHERE supplier_id=:supplier_id`, row)
	return err
}

func (t *supplierWriteTransaction) AuditSupplier(ctx context.Context, entry domain.SupplierAudit) error {
	changes := entry.Changes
	if entry.FullDiff {
		var err error
		if changes, err = createdModelChanges(ctx, t.tx, "application_supplierandcustomers", "supplier_id", entry.ID); err != nil {
			return err
		}
	}
	event := modelAuditEntry{Model: "supplierandcustomers", Key: entry.ID, Representation: "SupplierAndCustomers object (" + entry.ID + ")",
		Action: entry.Action, Changes: changes, Context: entry.Context, At: entry.At}
	if entry.Association != nil {
		path := entry.Path
		event.AssociationID, event.ResolutionPath = entry.Association, &path
	}
	return appendModelAudit(ctx, t.tx, event)
}
