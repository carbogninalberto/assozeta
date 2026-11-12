package repository

import (
	"context"
	"database/sql"
	"errors"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jackc/pgx/v5/pgconn"
	"github.com/jmoiron/sqlx"
)

type PersonaWrites struct{ db *sqlx.DB }

func NewPersonaWrites(db *sqlx.DB) *PersonaWrites { return &PersonaWrites{db: db} }

var _ domain.PersonaWritesStore = (*PersonaWrites)(nil)

type personaWritesTransaction struct{ *ownedTransaction }

func (r *PersonaWrites) BeginPersonaWrites(ctx context.Context) (domain.PersonaWritesTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &personaWritesTransaction{ownedTransaction: tx}, nil
}

func (t *personaWritesTransaction) Associate(ctx context.Context, id string, live bool, group *string) (*domain.PersonaAssociate, error) {
	var row domain.PersonaAssociate
	err := t.tx.GetContext(ctx, &row, `SELECT `+personaColumns("a")+` FROM application_associate a WHERE a.associate_id=$1
 AND (NOT $2 OR (NOT a.deleted AND ($3::uuid IS NULL OR a.group_id=$3::uuid))) FOR UPDATE`, id, live, group)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &row, nil
}

func (t *personaWritesTransaction) ActiveAssociationOf(ctx context.Context, user string) (*string, error) {
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

func (t *personaWritesTransaction) exists(ctx context.Context, query string, args ...any) (bool, error) {
	var found bool
	err := t.tx.GetContext(ctx, &found, `SELECT EXISTS(`+query+`)`, args...)
	return found, err
}

func (t *personaWritesTransaction) ActiveAssociationExists(ctx context.Context, id string) (bool, error) {
	return t.exists(ctx, `SELECT 1 FROM application_sportassociation WHERE sport_association_id=$1 AND NOT deleted`, id)
}

func (t *personaWritesTransaction) ActiveUserExists(ctx context.Context, id string) (bool, error) {
	return t.exists(ctx, `SELECT 1 FROM bakney_user WHERE user_id=$1 AND NOT deleted`, id)
}

func (t *personaWritesTransaction) GroupAssociation(ctx context.Context, id string) (string, bool, error) {
	var association string
	err := t.tx.GetContext(ctx, &association, `SELECT sport_association_id FROM application_group WHERE group_id=$1`, id)
	if errors.Is(err, sql.ErrNoRows) {
		return "", false, nil
	}
	return association, err == nil, err
}

func (t *personaWritesTransaction) FamilyExists(ctx context.Context, id string) (bool, error) {
	return t.exists(ctx, `SELECT 1 FROM application_family WHERE family_id=$1`, id)
}

// step runs one statement in a savepoint: a unique violation rolls it back
// and is ErrPersonaUniqueViolation.
func (t *personaWritesTransaction) step(ctx context.Context, run func() error) error {
	if _, err := t.tx.ExecContext(ctx, `SAVEPOINT persona_write`); err != nil {
		return err
	}
	if err := run(); err != nil {
		if _, rollback := t.tx.ExecContext(ctx, `ROLLBACK TO SAVEPOINT persona_write`); rollback != nil {
			return errors.Join(err, rollback)
		}
		var pg *pgconn.PgError
		if errors.As(err, &pg) && pg.Code == "23505" {
			return domain.ErrPersonaUniqueViolation
		}
		return err
	}
	_, err := t.tx.ExecContext(ctx, `RELEASE SAVEPOINT persona_write`)
	return err
}

const personaInsert = `INSERT INTO application_associate (associate_id, user_id, sport_association_id, picture_path, first_name,
 last_name, sex, tax_code, born_date, born_city, address_city, address, address_cap, nationality, nationality_residence, email, phone,
 phone_2, phone_3, phone_4, phone_label, phone_2_label, phone_3_label, phone_4_label, is_minor, disabled, draft, group_id, deleted,
 family_id, family_role, id_number, creation_date, migrated, notes)
 VALUES (:associate_id, :user_id, :sport_association_id, :picture_path, :first_name, :last_name, :sex, :tax_code, :born_date,
 :born_city, :address_city, :address, :address_cap, :nationality, :nationality_residence, :email, :phone, :phone_2, :phone_3, :phone_4,
 :phone_label, :phone_2_label, :phone_3_label, :phone_4_label, :is_minor, :disabled, :draft, :group_id, :deleted, :family_id,
 :family_role, :id_number, :creation_date, :migrated, :notes)`

func (t *personaWritesTransaction) InsertAssociate(ctx context.Context, row domain.PersonaAssociate) error {
	return t.step(ctx, func() error {
		_, err := t.tx.NamedExecContext(ctx, personaInsert, row)
		return err
	})
}

func (t *personaWritesTransaction) SaveAssociate(ctx context.Context, row domain.PersonaAssociate) error {
	return t.step(ctx, func() error {
		_, err := t.tx.NamedExecContext(ctx, `UPDATE application_associate SET user_id=:user_id, sport_association_id=:sport_association_id,
 picture_path=:picture_path, first_name=:first_name, last_name=:last_name, sex=:sex, tax_code=:tax_code, born_date=:born_date,
 born_city=:born_city, address_city=:address_city, address=:address, address_cap=:address_cap, nationality=:nationality,
 nationality_residence=:nationality_residence, email=:email, phone=:phone, phone_2=:phone_2, phone_3=:phone_3, phone_4=:phone_4,
 phone_label=:phone_label, phone_2_label=:phone_2_label, phone_3_label=:phone_3_label, phone_4_label=:phone_4_label,
 is_minor=:is_minor, disabled=:disabled, draft=:draft, group_id=:group_id, deleted=:deleted, family_id=:family_id,
 family_role=:family_role, id_number=:id_number, creation_date=:creation_date, migrated=:migrated, notes=:notes
 WHERE associate_id=:associate_id`, row)
		return err
	})
}

func (t *personaWritesTransaction) Relations(ctx context.Context, associate string) ([]domain.PersonaRelationRow, error) {
	rows := []domain.PersonaRelationRow{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT id, associate_id, tutor_id, is_primary FROM application_associatetutorrelation
 WHERE associate_id=$1 ORDER BY id FOR UPDATE`, associate)
	return rows, err
}

func (t *personaWritesTransaction) DeleteRelation(ctx context.Context, id int64) (map[string][2]string, error) {
	changes, _, err := deleteModelWithAudit(ctx, t.tx, "application_associatetutorrelation", "id", strconv.FormatInt(id, 10))
	return changes, err
}

func (t *personaWritesTransaction) InsertRelation(ctx context.Context, associate, tutor string, primary bool, audit func(int64) error) (int64, error) {
	var id int64
	err := t.step(ctx, func() error {
		if err := t.tx.GetContext(ctx, &id, `INSERT INTO application_associatetutorrelation (associate_id, tutor_id, is_primary)
 VALUES ($1, $2, $3) RETURNING id`, associate, tutor, primary); err != nil {
			return err
		}
		return audit(id)
	})
	return id, err
}

func (t *personaWritesTransaction) SetRelationPrimary(ctx context.Context, id int64, primary bool, audit func() error) error {
	return t.step(ctx, func() error {
		if err := audit(); err != nil {
			return err
		}
		_, err := t.tx.ExecContext(ctx, `UPDATE application_associatetutorrelation SET is_primary=$2 WHERE id=$1`, id, primary)
		return err
	})
}

func (t *personaWritesTransaction) BulkDelete(ctx context.Context, ids []string, association string, group *string) error {
	if len(ids) == 0 {
		return nil
	}
	_, err := t.tx.ExecContext(ctx, `UPDATE application_associate SET deleted=true WHERE associate_id = ANY(string_to_array($1, ',')::uuid[])
 AND sport_association_id=$2 AND NOT deleted AND ($3::uuid IS NULL OR group_id=$3::uuid)`, strings.Join(ids, ","), association, group)
	return err
}

func (t *personaWritesTransaction) Audit(ctx context.Context, entry domain.PersonaWriteAudit) error {
	changes := entry.Changes
	if entry.Created {
		var err error
		if changes, err = createdModelChanges(ctx, t.tx, "application_associate", "associate_id", entry.Key); err != nil {
			return err
		}
		if pair, ok := changes["group"]; ok && entry.SentGroup != "" {
			changes["group"] = [2]string{pair[0], entry.SentGroup}
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
