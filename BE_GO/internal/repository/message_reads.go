package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

type MessageReads struct{ db sqlx.QueryerContext }

func NewMessageReads(db sqlx.QueryerContext) *MessageReads { return &MessageReads{db: db} }

var _ domain.MessageReadStore = (*MessageReads)(nil)

func (r *MessageReads) VisitMessages(ctx context.Context, owner string, consume func(domain.MessageReadRecord) error) (err error) {
	if err = ctx.Err(); err != nil {
		return err
	}
	association, err := messageReadsAssociation(ctx, r.db, owner)
	if err != nil {
		return err
	}
	// Own query cancellation without canceling the caller. pgx Rows.Close drains
	// unread results, so early failures must cancel before closing the cursor.
	queryCtx, cancelQuery := context.WithCancel(ctx)
	defer cancelQuery()
	rows, err := r.db.QueryxContext(queryCtx, messageReadsListSQL, association)
	if err != nil {
		return err
	}
	defer func() {
		if err != nil {
			cancelQuery()
		}
		err = errors.Join(err, rows.Close())
	}()
	for rows.Next() {
		if err = ctx.Err(); err != nil {
			return err
		}
		var row messageReadRow
		if err = rows.StructScan(&row); err != nil {
			return err
		}
		if err = consume(row.record()); err != nil {
			return err
		}
		if err = ctx.Err(); err != nil {
			return err
		}
	}
	if err = rows.Err(); err != nil {
		return err
	}
	return ctx.Err()
}

func (r *MessageReads) MessageTransactions(ctx context.Context, owner, message string) ([]domain.MessageTransactionReadRecord, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	association, err := messageReadsAssociation(ctx, r.db, owner)
	if err != nil {
		return nil, err
	}
	var rows []messageTransactionReadRow
	if err = sqlx.SelectContext(ctx, r.db, &rows, messageReadsTransactionSQL, message, association); err != nil {
		return nil, err
	}
	result := make([]domain.MessageTransactionReadRecord, 0, len(rows))
	for _, row := range rows {
		result = append(result, row.record())
	}
	return result, nil
}

func messageReadsAssociation(ctx context.Context, db sqlx.QueryerContext, owner string) (string, error) {
	var association string
	err := sqlx.GetContext(ctx, db, &association,
		`SELECT sport_association_id FROM application_sportassociation WHERE user_id=$1 AND NOT deleted`, owner)
	if errors.Is(err, sql.ErrNoRows) {
		return "", domain.ErrAssociationNotFound
	}
	return association, err
}

type messageReadRow struct {
	ID            uuid.UUID  `db:"message_id"`
	AssociationID uuid.UUID  `db:"sport_association_id"`
	Type          string     `db:"type"`
	Message       string     `db:"message"`
	Subject       *string    `db:"subject"`
	CreatedAt     *time.Time `db:"created_at"`
}

func (r messageReadRow) record() domain.MessageReadRecord {
	return domain.MessageReadRecord{
		ID: r.ID, AssociationID: r.AssociationID, Type: r.Type, Message: r.Message,
		Subject: r.Subject, CreatedAt: r.CreatedAt,
	}
}

type messageTransactionReadRow struct {
	ID            uuid.UUID  `db:"message_transaction_id"`
	MessageID     uuid.UUID  `db:"message_id"`
	Recipient     string     `db:"recipient"`
	SentOn        time.Time  `db:"sent_on"`
	DeliveredOn   *time.Time `db:"delivered_on"`
	OpenedOn      *time.Time `db:"opened_on"`
	ClickedOn     *time.Time `db:"clicked_on"`
	BouncedOn     *time.Time `db:"bounced_on"`
	BouncedReason *string    `db:"bounced_reason"`
	BouncedType   *string    `db:"bounced_type"`
	BouncedStatus *string    `db:"bounced_status"`
}

func (r messageTransactionReadRow) record() domain.MessageTransactionReadRecord {
	return domain.MessageTransactionReadRecord{
		ID: r.ID, MessageID: r.MessageID, Recipient: r.Recipient, SentOn: r.SentOn,
		DeliveredOn: r.DeliveredOn, OpenedOn: r.OpenedOn, ClickedOn: r.ClickedOn,
		BouncedOn: r.BouncedOn, BouncedReason: r.BouncedReason, BouncedType: r.BouncedType,
		BouncedStatus: r.BouncedStatus,
	}
}

const messageReadsListSQL = `SELECT m.message_id,m.sport_association_id,m.type,m.message,m.subject,m.created_at
 FROM communications_message m
 WHERE m.sport_association_id=$1 AND m.type IN ('EMAIL', 'INSIDE_APP')
 ORDER BY m.created_at DESC`

const messageReadsTransactionSQL = `SELECT t.message_transaction_id,t.message_id,t.recipient,t.sent_on,
 t.delivered_on,t.opened_on,t.clicked_on,t.bounced_on,t.bounced_reason,t.bounced_type,t.bounced_status
 FROM communications_messagetransaction t
 JOIN communications_message m ON m.message_id=t.message_id AND m.sport_association_id=$2
 WHERE t.message_id=$1 AND m.type IN ('EMAIL', 'INSIDE_APP')
 ORDER BY t.sent_on DESC, t.message_transaction_id`
