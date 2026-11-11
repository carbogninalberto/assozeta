package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type SubscriptionMemberships struct{ db *sqlx.DB }

func NewSubscriptionMemberships(db *sqlx.DB) *SubscriptionMemberships {
	return &SubscriptionMemberships{db: db}
}

type subscriptionMembershipTransaction struct{ *paymentCategoryTransaction }

func (r *SubscriptionMemberships) BeginSubscriptionMembership(ctx context.Context) (domain.SubscriptionMembershipTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &subscriptionMembershipTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

func (t *subscriptionMembershipTransaction) OwnerAssociation(ctx context.Context, owner string) (string, error) {
	return paymentCategoryAssociation(ctx, t.tx, owner, nil, false)
}

// The M2M through table name is Django's truncate_name(..., 63).
const subscriptionMembershipDocuments = `COALESCE((SELECT json_agg(json_build_object('document_id', d.document_id,
 'creation_date', d.creation_date, 'filepath', d.filepath, 'filename', d.filename, 'file_size_bytes', d.file_size_bytes, 'token', d.token))
  FROM application_subscriptionmembership_attached_membership_docu749f l JOIN docmanager_document d ON d.document_id=l.document_id
  WHERE l.subscriptionmembership_id=m.subscription_membership_id), '[]'::json)::text`

func (t *subscriptionMembershipTransaction) Memberships(ctx context.Context, association string, filter domain.SubscriptionMembershipFilter,
	id *string) ([]domain.SubscriptionMembershipRow, error) {
	rows := []struct {
		ID               string    `db:"id"`
		SubscriptionID   *string   `db:"subscription_id"`
		AssociationID    string    `db:"sport_association_id"`
		AssociateID      *string   `db:"associate_id"`
		PaymentID        *string   `db:"payment_id"`
		CreationDate     time.Time `db:"creation_date"`
		StartDate        *string   `db:"start_date"`
		EndDate          *string   `db:"end_date"`
		MembershipType   *string   `db:"membership_type"`
		MembershipNumber *string   `db:"membership_number"`
		Description      *string   `db:"description"`
		Price            *string   `db:"price"`
		Paid             *bool     `db:"paid"`
		Documents        string    `db:"documents"`
	}{}
	query := `SELECT m.subscription_membership_id::text AS id, m.subscription_id::text AS subscription_id,
 m.sport_association_id::text AS sport_association_id, m.associate_id::text AS associate_id, m.payment_id::text AS payment_id,
 m.creation_date, m.start_date::text AS start_date, m.end_date::text AS end_date, m.membership_type, m.membership_number,
 m.description, m.price::text AS price, p.paid, ` + subscriptionMembershipDocuments + ` AS documents
 FROM application_subscriptionmembership m LEFT JOIN application_payment p ON p.payment_id=m.payment_id
 WHERE m.sport_association_id=$1 AND ($2::uuid IS NULL OR m.subscription_id=$2::uuid)
 AND ($3::uuid IS NULL OR m.associate_id=$3::uuid) AND ($4::uuid IS NULL OR m.subscription_membership_id=$4::uuid)`
	if id != nil {
		query += ` FOR UPDATE OF m`
	}
	if err := t.tx.SelectContext(ctx, &rows, query, association, filter.Subscription, filter.Associate, id); err != nil {
		return nil, err
	}
	result := make([]domain.SubscriptionMembershipRow, 0, len(rows))
	for _, row := range rows {
		result = append(result, domain.SubscriptionMembershipRow{SubscriptionMembership: domain.SubscriptionMembership{
			ID: row.ID, SubscriptionID: row.SubscriptionID, AssociationID: row.AssociationID, AssociateID: row.AssociateID,
			PaymentID: row.PaymentID, CreationDate: row.CreationDate, StartDate: row.StartDate, EndDate: row.EndDate,
			MembershipType: row.MembershipType, MembershipNumber: row.MembershipNumber, Description: row.Description, Price: row.Price},
			Paid: row.Paid, Documents: row.Documents})
	}
	return result, nil
}

func (t *subscriptionMembershipTransaction) reference(ctx context.Context, query string, args ...any) (*domain.SubscriptionMembershipReference, error) {
	var association *string
	err := t.tx.GetContext(ctx, &association, query, args...)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &domain.SubscriptionMembershipReference{AssociationID: association}, nil
}

// Association uses SportAssociation's default manager (original_objects:
// deleted associations included).
func (t *subscriptionMembershipTransaction) Association(ctx context.Context, id string) (*domain.SubscriptionMembershipReference, error) {
	return t.reference(ctx, `SELECT sport_association_id::text FROM application_sportassociation WHERE sport_association_id=$1`, id)
}

// Associate uses SoftDeleteGroupAwareManager.
func (t *subscriptionMembershipTransaction) Associate(ctx context.Context, id string, group *string) (*domain.SubscriptionMembershipReference, error) {
	return t.reference(ctx, `SELECT sport_association_id::text FROM application_associate
 WHERE associate_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid)`, id, group)
}

// Payment uses PaymentManager (live, group-aware).
func (t *subscriptionMembershipTransaction) Payment(ctx context.Context, id string, group *string) (*domain.SubscriptionMembershipReference, error) {
	return t.reference(ctx, `SELECT sport_association_id::text FROM application_payment
 WHERE payment_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid)`, id, group)
}

// PaymentPaid is payment.paid through the forward relation (base manager).
func (t *subscriptionMembershipTransaction) PaymentPaid(ctx context.Context, id string) (*bool, error) {
	var paid *bool
	err := t.tx.GetContext(ctx, &paid, `SELECT paid FROM application_payment WHERE payment_id=$1`, id)
	return paid, err
}

func (t *subscriptionMembershipTransaction) MembershipDocuments(ctx context.Context, id string) (string, error) {
	var documents string
	err := t.tx.GetContext(ctx, &documents, `SELECT `+subscriptionMembershipDocuments+` FROM application_subscriptionmembership m
 WHERE m.subscription_membership_id=$1`, id)
	return documents, err
}

func subscriptionMembershipAudit(id string, action int, changes map[string][2]string, association *string, path string,
	audit domain.LoginAuditContext, at time.Time) modelAuditEntry {
	entry := modelAuditEntry{Model: "subscriptionmembership", Key: id, Representation: "SubscriptionMembership object (" + id + ")",
		Action: action, Changes: changes, Context: audit, At: at}
	if association != nil {
		entry.AssociationID, entry.ResolutionPath = association, &path
	}
	return entry
}

func (t *subscriptionMembershipTransaction) CreateMembership(ctx context.Context, row domain.SubscriptionMembership, audit domain.LoginAuditContext,
	at time.Time) error {
	return t.insertMembership(ctx, row, audit, at, false)
}

func (t *subscriptionMembershipTransaction) insertMembership(ctx context.Context, row domain.SubscriptionMembership, audit domain.LoginAuditContext,
	at time.Time, copied bool) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO application_subscriptionmembership (subscription_membership_id, subscription_id,
 sport_association_id, creation_date, start_date, end_date, membership_type, membership_number, description, price, payment_id, associate_id)
 VALUES ($1,$2,$3,$4,$5::date,$6::date,$7,$8,$9,$10::numeric,$11,$12)`, row.ID, row.SubscriptionID, row.AssociationID, row.CreationDate,
		row.StartDate, row.EndDate, row.MembershipType, row.MembershipNumber, row.Description, row.Price, row.PaymentID, row.AssociateID); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "application_subscriptionmembership", "subscription_membership_id", row.ID)
	if err != nil {
		return err
	}
	if row.Price != nil {
		// auditlog renders the instance's Decimal (a negative zero keeps its sign).
		changes["price"] = [2]string{"None", *row.Price}
	}
	if copied {
		if err = appendModelAudit(ctx, t.tx, subscriptionMembershipAudit(row.ID, 1, changes, &row.AssociationID, "changes_json.sport_association", audit, at)); err != nil {
			return err
		}
	}
	return appendModelAudit(ctx, t.tx, subscriptionMembershipAudit(row.ID, 0, changes, &row.AssociationID, "direct", audit, at))
}

// SaveMembership is Model.save(): every field is written, auditlog records
// the changed ones only.
func (t *subscriptionMembershipTransaction) SaveMembership(ctx context.Context, row domain.SubscriptionMembership, changes map[string][2]string,
	audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `UPDATE application_subscriptionmembership SET subscription_id=$2, sport_association_id=$3,
 creation_date=$4, start_date=$5::date, end_date=$6::date, membership_type=$7, membership_number=$8, description=$9, price=$10::numeric,
 payment_id=$11, associate_id=$12 WHERE subscription_membership_id=$1`, row.ID, row.SubscriptionID, row.AssociationID, row.CreationDate,
		row.StartDate, row.EndDate, row.MembershipType, row.MembershipNumber, row.Description, row.Price, row.PaymentID, row.AssociateID); err != nil {
		return err
	}
	if len(changes) == 0 {
		return nil
	}
	return appendModelAudit(ctx, t.tx, subscriptionMembershipAudit(row.ID, 1, changes, &row.AssociationID, "direct", audit, at))
}

func (t *subscriptionMembershipTransaction) DeleteMembership(ctx context.Context, id string, audit domain.LoginAuditContext, at time.Time) error {
	// The collector deletes the document links first; documents stay.
	if _, err := t.tx.ExecContext(ctx, `DELETE FROM application_subscriptionmembership_attached_membership_docu749f
 WHERE subscriptionmembership_id=$1`, id); err != nil {
		return err
	}
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_subscriptionmembership", "subscription_membership_id", id)
	if err != nil {
		return err
	}
	var association *string
	if value := changes["sport_association"][0]; value != "" && value != "None" {
		association = &value
	}
	return appendModelAudit(ctx, t.tx, subscriptionMembershipAudit(id, 2, changes, association, "changes_json.sport_association", audit, at))
}

func (t *subscriptionMembershipTransaction) CreateMembershipDocument(ctx context.Context, document domain.NewDocument,
	audit domain.LoginAuditContext, at time.Time) error {
	if _, err := t.tx.ExecContext(ctx, `INSERT INTO docmanager_document (document_id, creation_date, filepath, filename, file_size_bytes, token)
 VALUES ($1, $2, NULL, $3, NULL, $4)`, document.ID, document.Created, document.Filename, document.Token); err != nil {
		return err
	}
	changes, err := createdModelChanges(ctx, t.tx, "docmanager_document", "document_id", document.ID)
	if err != nil {
		return err
	}
	for name, rendered := range documentReverse {
		changes[name] = [2]string{"None", rendered}
	}
	var actorAssociation *string
	if audit.ActorID != nil {
		var id string
		err := t.tx.GetContext(ctx, &id, `SELECT sport_association_id::text FROM application_sportassociation
 WHERE user_id=$1 AND NOT deleted ORDER BY sport_association_id LIMIT 1`, *audit.ActorID)
		if err == nil {
			actorAssociation = &id
		} else if !errors.Is(err, sql.ErrNoRows) {
			return err
		}
	}
	return appendModelAuditForApp(ctx, t.tx, "docmanager", documentAudit(document.ID, 0, changes, actorAssociation, audit, at))
}

func (t *subscriptionMembershipTransaction) LinkMembershipDocument(ctx context.Context, membership, document string) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_subscriptionmembership_attached_membership_docu749f
 (subscriptionmembership_id, document_id) VALUES ($1, $2) ON CONFLICT DO NOTHING`, membership, document)
	return err
}
