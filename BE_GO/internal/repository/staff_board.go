package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type StaffBoard struct{ db *sqlx.DB }

func NewStaffBoard(db *sqlx.DB) *StaffBoard { return &StaffBoard{db: db} }

var _ domain.StaffBoardStore = (*StaffBoard)(nil)

// One joined query: author (nullable after SET_NULL) and the association owner
// and denomination for author_name. Ordering matches the model Meta.
const staffBoardSelect = `SELECT m.staff_board_message_id,m.sport_association_id,a.user_id AS association_owner_id,
a.denomination,m.author_id,u.first_name,u.last_name,u.username,u.avatar_image,m.content,m.document,m.pinned,
m.created_at,m.updated_at
FROM communications_staffboardmessage m
JOIN application_sportassociation a ON a.sport_association_id=m.sport_association_id
LEFT JOIN bakney_user u ON u.user_id=m.author_id`

const staffBoardListSQL = staffBoardSelect + `
WHERE m.sport_association_id=$1 ORDER BY m.pinned DESC,m.created_at DESC`

type staffBoardRow struct {
	ID           string    `db:"staff_board_message_id"`
	Association  string    `db:"sport_association_id"`
	Owner        string    `db:"association_owner_id"`
	Denomination string    `db:"denomination"`
	AuthorID     *string   `db:"author_id"`
	FirstName    *string   `db:"first_name"`
	LastName     *string   `db:"last_name"`
	Username     *string   `db:"username"`
	Avatar       *string   `db:"avatar_image"`
	Content      string    `db:"content"`
	Document     *[]byte   `db:"document"`
	Pinned       bool      `db:"pinned"`
	CreatedAt    time.Time `db:"created_at"`
	UpdatedAt    time.Time `db:"updated_at"`
}

func (r staffBoardRow) message() domain.StaffBoardMessage {
	message := domain.StaffBoardMessage{ID: r.ID, AssociationID: r.Association, AssociationOwnerID: r.Owner,
		AssociationDenomination: r.Denomination, Content: r.Content, Pinned: r.Pinned, CreatedAt: r.CreatedAt, UpdatedAt: r.UpdatedAt}
	if r.Document != nil {
		message.Document = json.RawMessage(*r.Document)
	}
	if r.AuthorID != nil {
		message.Author = &domain.StaffBoardAuthor{ID: *r.AuthorID, FirstName: deref(r.FirstName), LastName: deref(r.LastName),
			Username: deref(r.Username), AvatarImage: r.Avatar}
	}
	return message
}

func deref(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}

func (r *StaffBoard) StaffBoardMessages(ctx context.Context, owner string) ([]domain.StaffBoardMessage, error) {
	association, err := messageReadsAssociation(ctx, r.db, owner)
	if err != nil {
		return nil, err
	}
	var rows []staffBoardRow
	if err = r.db.SelectContext(ctx, &rows, staffBoardListSQL, association); err != nil {
		return nil, err
	}
	messages := make([]domain.StaffBoardMessage, 0, len(rows))
	for _, row := range rows {
		messages = append(messages, row.message())
	}
	return messages, nil
}

func (r *StaffBoard) BeginStaffBoardMutations(ctx context.Context) (domain.StaffBoardTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &staffBoardTransaction{paymentCategoryTransaction: &paymentCategoryTransaction{ownedTransaction: tx}}, nil
}

// Reuse the shared owner/association lock and owned transaction, including the
// restore advisory barrier and cleanup join.
type staffBoardTransaction struct{ *paymentCategoryTransaction }

var _ domain.StaffBoardTransaction = (*staffBoardTransaction)(nil)

func (t *staffBoardTransaction) OwnerForUpdate(ctx context.Context, id string) (int, bool, bool, error) {
	role, active, deleted, err := t.paymentCategoryTransaction.OwnerForUpdate(ctx, id)
	if errors.Is(err, domain.ErrPaymentCategoryForbidden) {
		err = domain.ErrStaffBoardForbidden
	}
	return role, active, deleted, err
}

func (t *staffBoardTransaction) StaffBoardMessageForUpdate(ctx context.Context, id, association string) (domain.StaffBoardMessage, error) {
	var row staffBoardRow
	err := t.tx.GetContext(ctx, &row, staffBoardSelect+`
WHERE m.staff_board_message_id=$1 AND m.sport_association_id=$2 FOR UPDATE OF m`, id, association)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.StaffBoardMessage{}, domain.ErrStaffBoardNotFound
	}
	return row.message(), err
}

func staffBoardDocument(value json.RawMessage) any {
	if value == nil {
		return nil
	}
	return string(value)
}

func (t *staffBoardTransaction) InsertStaffBoardMessage(ctx context.Context, row domain.StaffBoardWrite) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO communications_staffboardmessage
(staff_board_message_id,sport_association_id,author_id,content,document,pinned,created_at,updated_at)
VALUES($1,$2,$3,$4,$5::jsonb,$6,$7,$7)`, row.ID, row.AssociationID, row.AuthorID, row.Content,
		staffBoardDocument(row.Document), row.Pinned, row.At)
	return err
}

func (t *staffBoardTransaction) UpdateStaffBoardMessage(ctx context.Context, id, association string, change domain.StaffBoardChange) error {
	var content, pinned any
	if change.Content != nil {
		content = *change.Content
	}
	if change.Pinned != nil {
		pinned = *change.Pinned
	}
	result, err := t.tx.ExecContext(ctx, `UPDATE communications_staffboardmessage SET
content=CASE WHEN $3::boolean THEN $4 ELSE content END,
document=CASE WHEN $5::boolean THEN $6::jsonb ELSE document END,
pinned=CASE WHEN $7::boolean THEN $8 ELSE pinned END,
updated_at=$9
WHERE staff_board_message_id=$1 AND sport_association_id=$2`, id, association,
		change.Content != nil, content, change.DocumentSet, staffBoardDocument(change.Document),
		change.Pinned != nil, pinned, change.At)
	return requireOneRow(result, err)
}

func (t *staffBoardTransaction) DeleteStaffBoardMessage(ctx context.Context, id, association string) error {
	result, err := t.tx.ExecContext(ctx, `DELETE FROM communications_staffboardmessage
WHERE staff_board_message_id=$1 AND sport_association_id=$2`, id, association)
	return requireOneRow(result, err)
}

// AppendRealtimeEvent inserts a version-1 envelope and notifies listeners; both
// take effect only at commit.
func (t *staffBoardTransaction) AppendRealtimeEvent(ctx context.Context, kind, target string) error {
	_, err := t.tx.ExecContext(ctx, `WITH event AS (
INSERT INTO public.assozeta_go_realtime_events(envelope_version,kind,target_id) VALUES(1,$1,$2) RETURNING id)
SELECT pg_notify('assozeta_go_realtime', id::text) FROM event`, kind, target)
	return err
}

func requireOneRow(result sql.Result, err error) error {
	if err != nil {
		return err
	}
	count, err := result.RowsAffected()
	if err == nil && count != 1 {
		return domain.ErrStaffBoardNotFound
	}
	return err
}
