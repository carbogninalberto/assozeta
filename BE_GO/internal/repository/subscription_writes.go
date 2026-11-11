package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type SubscriptionEdits struct{ db *sqlx.DB }

func NewSubscriptionEdits(db *sqlx.DB) *SubscriptionEdits { return &SubscriptionEdits{db: db} }

type subscriptionEditTransaction struct{ *ownedTransaction }

func (r *SubscriptionEdits) BeginSubscriptionEdit(ctx context.Context) (domain.SubscriptionEditTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &subscriptionEditTransaction{ownedTransaction: tx}, nil
}

const subscriptionEditColumns = `associate_id, user_id, sport_association_id, first_name, last_name, sex, tax_code, born_date, born_city,
 address_city, address, address_cap, email, phone, is_minor, group_id, deleted, family_id, family_role, creation_date`

// associateReverse are Associate's reverse foreign keys that declare a
// related_name; auditlog renders their empty manager on create and delete.
var associateReverse = map[string]string{
	"tutor_relations":   "application.AssociateTutorRelation.None",
	"tutored_relations": "application.AssociateTutorRelation.None",
}

func (t *subscriptionEditTransaction) Associate(ctx context.Context, id string, group *string, association string) (*domain.SubscriptionEditAssociate, error) {
	var row domain.SubscriptionEditAssociate
	err := t.tx.GetContext(ctx, &row, `SELECT `+subscriptionEditColumns+` FROM application_associate
 WHERE associate_id=$1 AND NOT deleted AND ($2::uuid IS NULL OR group_id=$2::uuid)
 AND ($3::text='' OR sport_association_id::text=$3::text) FOR UPDATE`, id, group, association)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *subscriptionEditTransaction) ActiveAssociationOf(ctx context.Context, user string) (*string, error) {
	var id string
	err := t.tx.GetContext(ctx, &id, `SELECT sport_association_id FROM application_sportassociation WHERE user_id=$1 AND NOT deleted
 ORDER BY sport_association_id LIMIT 1`, user)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &id, nil
}

func (t *subscriptionEditTransaction) exists(ctx context.Context, query string, args ...any) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(`+query+`)`, args...)
	return found, err
}

func (t *subscriptionEditTransaction) AssociationExists(ctx context.Context, id string) (bool, error) {
	return t.exists(ctx, `SELECT 1 FROM application_sportassociation WHERE sport_association_id=$1`, id)
}

func (t *subscriptionEditTransaction) ActiveAssociationExists(ctx context.Context, id string) (bool, error) {
	return t.exists(ctx, `SELECT 1 FROM application_sportassociation WHERE sport_association_id=$1 AND NOT deleted`, id)
}

func (t *subscriptionEditTransaction) FamilyExists(ctx context.Context, id string) (bool, error) {
	return t.exists(ctx, `SELECT 1 FROM application_family WHERE family_id=$1`, id)
}

func (t *subscriptionEditTransaction) GroupExists(ctx context.Context, id string) (bool, error) {
	return t.exists(ctx, `SELECT 1 FROM application_group WHERE group_id=$1`, id)
}

func (t *subscriptionEditTransaction) TaxCodeMatches(ctx context.Context, taxCode string, association, group *string) ([]domain.SubscriptionEditAssociate, error) {
	rows := []domain.SubscriptionEditAssociate{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT `+subscriptionEditColumns+` FROM application_associate
 WHERE UPPER(tax_code::text)=UPPER($1) AND sport_association_id IS NOT DISTINCT FROM $2::uuid AND NOT deleted
 AND ($3::uuid IS NULL OR group_id=$3::uuid) ORDER BY creation_date DESC FOR UPDATE`, taxCode, association, group)
	return rows, err
}

func (t *subscriptionEditTransaction) NewestTaxCodeMatch(ctx context.Context, taxCode string, association, group *string) (*domain.SubscriptionEditAssociate, error) {
	var row domain.SubscriptionEditAssociate
	err := t.tx.GetContext(ctx, &row, `SELECT `+subscriptionEditColumns+` FROM application_associate
 WHERE UPPER(tax_code::text)=UPPER($1) AND sport_association_id IS NOT DISTINCT FROM $2::uuid AND NOT deleted
 AND ($3::uuid IS NULL OR group_id=$3::uuid) ORDER BY creation_date DESC LIMIT 1`, taxCode, association, group)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

// DeleteTutorCollected is Associate.delete() through the Collector port (collaborator-delete-go): the SET_NULL updates, the CASCADE
// rows (payments and their receivers included) and every auditlog entry.
func (t *subscriptionEditTransaction) DeleteTutorCollected(ctx context.Context, tutor string, audit domain.LoginAuditContext, group *string,
	now func() time.Time) error {
	run := &collaboratorDeleteRun{tx: t.tx, graph: collaboratorDeleteGraphLoaded, audit: audit, group: group, now: now}
	model := collaboratorDeleteGraphLoaded.Models["application.Associate"]
	rows, err := run.load(ctx, model, `t.associate_id = $1`, tutor)
	if err != nil {
		return err
	}
	return run.Delete(ctx, "application.Associate", rows)
}

func (t *subscriptionEditTransaction) AssociateSnapshot(ctx context.Context, associate string) (string, error) {
	var snapshot string
	err := t.tx.GetContext(ctx, &snapshot, `SELECT to_jsonb(a)::text FROM application_associate a WHERE associate_id=$1`, associate)
	return snapshot, err
}

func (t *subscriptionEditTransaction) RestoreAssociate(ctx context.Context, snapshot string) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO application_associate SELECT * FROM jsonb_populate_record(NULL::application_associate,
 jsonb_set($1::jsonb, '{is_minor}', 'true'))`, snapshot)
	return err
}

func (t *subscriptionEditTransaction) Relations(ctx context.Context, associate string) ([]domain.SubscriptionEditRelation, error) {
	rows := []domain.SubscriptionEditRelation{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT id, associate_id, tutor_id, is_primary FROM application_associatetutorrelation
 WHERE associate_id=$1 ORDER BY id FOR UPDATE`, associate)
	return rows, err
}

// OtherReferences reports a row outside the tutor relations that references the associate (SET_NULL subscriptions, memberships
// and invoices, CASCADE payments): the delete then goes through the Collector port.
func (t *subscriptionEditTransaction) OtherReferences(ctx context.Context, associate string) (bool, error) {
	refs, err := modelReferences(ctx, t.tx, "application_associate", "associate_id")
	if err != nil {
		return false, err
	}
	for _, ref := range refs {
		if ref.Table == "application_associatetutorrelation" && (ref.Column == "associate_id" || ref.Column == "tutor_id") {
			continue
		}
		found, err := t.exists(ctx, `SELECT 1 FROM `+pq(ref.Table)+` WHERE `+pq(ref.Column)+`=$1`, associate)
		if err != nil || found {
			return found, err
		}
	}
	return false, nil
}

// InsertAssociate stores a new Associate with the model defaults of the
// columns the serializer does not write.
func (t *subscriptionEditTransaction) InsertAssociate(ctx context.Context, row domain.SubscriptionEditAssociate) error {
	_, err := t.tx.NamedExecContext(ctx, `INSERT INTO application_associate (associate_id, user_id, sport_association_id, picture_path,
 first_name, last_name, sex, tax_code, born_date, born_city, address_city, address, address_cap, nationality, nationality_residence, email,
 phone, phone_2, phone_3, phone_4, phone_label, phone_2_label, phone_3_label, phone_4_label, is_minor, disabled, draft, group_id, deleted,
 family_id, family_role, id_number, creation_date, migrated, notes)
 VALUES (:associate_id, :user_id, :sport_association_id, NULL, :first_name, :last_name, :sex, :tax_code, :born_date, :born_city,
 :address_city, :address, :address_cap, 'Italiana', 'Italia', :email, :phone, NULL, NULL, NULL, NULL, NULL, NULL, NULL, :is_minor, false,
 false, :group_id, :deleted, :family_id, :family_role, NULL, :creation_date, false, NULL)`, row)
	return err
}

func (t *subscriptionEditTransaction) SaveAssociate(ctx context.Context, row domain.SubscriptionEditAssociate) error {
	_, err := t.tx.NamedExecContext(ctx, `UPDATE application_associate SET user_id=:user_id, sport_association_id=:sport_association_id,
 first_name=:first_name, last_name=:last_name, sex=:sex, tax_code=:tax_code, born_date=:born_date, born_city=:born_city,
 address_city=:address_city, address=:address, address_cap=:address_cap, email=:email, phone=:phone, is_minor=:is_minor,
 family_id=:family_id, family_role=:family_role WHERE associate_id=:associate_id`, row)
	return err
}

func (t *subscriptionEditTransaction) PromoteRelation(ctx context.Context, id int64) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE application_associatetutorrelation SET is_primary=true WHERE id=$1`, id)
	return err
}

func (t *subscriptionEditTransaction) DeleteTutor(ctx context.Context, tutor string) ([]domain.SubscriptionEditRelationDelete, map[string][2]string, error) {
	ids := []int64{}
	if err := t.tx.SelectContext(ctx, &ids, `SELECT id FROM application_associatetutorrelation WHERE associate_id=$1 OR tutor_id=$1
 ORDER BY id DESC FOR UPDATE`, tutor); err != nil {
		return nil, nil, err
	}
	relations := make([]domain.SubscriptionEditRelationDelete, 0, len(ids))
	for _, id := range ids {
		changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_associatetutorrelation", "id", strconv.FormatInt(id, 10))
		if err != nil {
			return nil, nil, err
		}
		relations = append(relations, domain.SubscriptionEditRelationDelete{ID: id, Changes: changes})
	}
	changes, _, err := deleteModelWithAuditReverse(ctx, t.tx, "application_associate", "associate_id", tutor, associateReverse)
	if err != nil {
		return nil, nil, err
	}
	return relations, changes, nil
}

func (t *subscriptionEditTransaction) Audit(ctx context.Context, entry domain.SubscriptionEditAudit) error {
	changes := entry.Changes
	if entry.Created {
		var err error
		if changes, err = createdModelChanges(ctx, t.tx, "application_associate", "associate_id", entry.Key); err != nil {
			return err
		}
		for name, manager := range associateReverse {
			changes[name] = [2]string{"None", manager}
		}
	}
	name := "Associate"
	if entry.Model == "associatetutorrelation" {
		name = "AssociateTutorRelation"
	}
	event := modelAuditEntry{Model: entry.Model, Key: entry.Key, Representation: name + " object (" + entry.Key + ")", Action: entry.Action,
		Changes: changes, Context: entry.Context, At: entry.At}
	if entry.Association != nil {
		path := entry.Path
		event.AssociationID, event.ResolutionPath = entry.Association, &path
	}
	return appendModelAudit(ctx, t.tx, event)
}
