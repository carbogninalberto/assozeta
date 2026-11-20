package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
)

type MessageTemplates struct{ db *sqlx.DB }

func NewMessageTemplates(db *sqlx.DB) *MessageTemplates { return &MessageTemplates{db: db} }

func (r *MessageTemplates) BeginMessageTemplates(ctx context.Context) (domain.MessageTemplateTransaction, error) {
	tx, err := beginOwnedTransaction(ctx, r.db, &sql.TxOptions{Isolation: sql.LevelReadCommitted})
	if err != nil {
		return nil, err
	}
	return &messageTemplateTransaction{tx}, nil
}

type messageTemplateTransaction struct{ *ownedTransaction }

const messageTemplateColumns = "id,channel,locale,name,subject,body,enabled,is_default"

func (t *messageTemplateTransaction) Association(ctx context.Context, owner string, scope *string, lock bool) (string, string, error) {
	id, err := paymentCategoryAssociation(ctx, t.tx, owner, scope, lock)
	if err != nil {
		return "", "", err
	}
	var name string
	err = t.tx.GetContext(ctx, &name, `SELECT COALESCE(denomination, '') FROM application_sportassociation WHERE sport_association_id=$1`, id)
	return id, name, err
}

func (t *messageTemplateTransaction) HasTemplates(ctx context.Context, association string) (bool, error) {
	var exists bool
	err := t.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM communications_messagetemplate WHERE sport_association_id=$1)`, association)
	return exists, err
}

func (t *messageTemplateTransaction) Templates(ctx context.Context, association string, enabledOnly bool) ([]domain.MessageTemplate, error) {
	rows := []domain.MessageTemplate{}
	err := t.tx.SelectContext(ctx, &rows, `SELECT `+messageTemplateColumns+`
FROM communications_messagetemplate WHERE sport_association_id=$1 AND NOT archived AND (NOT $2 OR enabled)
ORDER BY channel,locale,id`, association, enabledOnly)
	return rows, err
}

func (t *messageTemplateTransaction) InsertTemplate(ctx context.Context, association string, row domain.MessageTemplate, audit domain.LoginAuditContext, at time.Time) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO communications_messagetemplate
(id,sport_association_id,channel,locale,name,subject,body,enabled,is_default,archived,updated_by_id,updated_at)
VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,false,$10,$11)`,
		row.ID, association, row.Channel, row.Locale, row.Name, row.Subject, row.Body, row.Enabled, row.IsDefault, audit.ActorID, at)
	if err != nil || audit.ActorID == nil {
		return err
	}
	return t.auditTemplate(ctx, association, row.ID, nil, templateAuditValues(row, false), audit, at)
}

func (t *messageTemplateTransaction) UpdateTemplate(ctx context.Context, association string, before, row domain.MessageTemplate, audit domain.LoginAuditContext, at time.Time) error {
	return t.updateTemplate(ctx, association, before, row, false, audit, at)
}

func (t *messageTemplateTransaction) updateTemplate(ctx context.Context, association string, before, row domain.MessageTemplate, archived bool, audit domain.LoginAuditContext, at time.Time) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE communications_messagetemplate SET channel=$3,locale=$4,name=$5,subject=$6,
body=$7,enabled=$8,is_default=$9,archived=$10,updated_by_id=$11,updated_at=$12 WHERE id=$1 AND sport_association_id=$2 AND NOT archived`,
		row.ID, association, row.Channel, row.Locale, row.Name, row.Subject, row.Body, row.Enabled, row.IsDefault, archived, audit.ActorID, at)
	if err != nil {
		return err
	}
	return t.auditTemplate(ctx, association, row.ID, templateAuditValues(before, false), templateAuditValues(row, archived), audit, at)
}

func (t *messageTemplateTransaction) ClearDefault(ctx context.Context, association string, row domain.MessageTemplate, audit domain.LoginAuditContext, at time.Time) error {
	var defaults []domain.MessageTemplate
	err := t.tx.SelectContext(ctx, &defaults, `SELECT `+messageTemplateColumns+` FROM communications_messagetemplate
WHERE sport_association_id=$1 AND channel=$2 AND locale=$3 AND id<>$4 AND is_default AND NOT archived ORDER BY id`,
		association, row.Channel, row.Locale, row.ID)
	if err != nil {
		return err
	}
	for _, before := range defaults {
		after := before
		after.IsDefault = false
		if err := t.UpdateTemplate(ctx, association, before, after, audit, at); err != nil {
			return err
		}
	}
	return nil
}

func (t *messageTemplateTransaction) ArchiveTemplate(ctx context.Context, association, id string, audit domain.LoginAuditContext, at time.Time) error {
	var before domain.MessageTemplate
	err := t.tx.GetContext(ctx, &before, `SELECT `+messageTemplateColumns+` FROM communications_messagetemplate
WHERE sport_association_id=$1 AND id=$2 AND NOT archived`, association, id)
	if errors.Is(err, sql.ErrNoRows) {
		return domain.ErrMessageTemplateNotFound
	}
	if err != nil {
		return err
	}
	after := before
	after.Enabled, after.IsDefault = false, false
	return t.updateTemplate(ctx, association, before, after, true, audit, at)
}

func templateAuditValues(row domain.MessageTemplate, archived bool) map[string]string {
	return map[string]string{"channel": row.Channel, "locale": row.Locale, "name": row.Name, "subject": row.Subject,
		"body": row.Body, "enabled": pythonBool(row.Enabled), "is_default": pythonBool(row.IsDefault), "archived": pythonBool(archived)}
}

func (t *messageTemplateTransaction) auditTemplate(ctx context.Context, association, id string, before, after map[string]string, audit domain.LoginAuditContext, at time.Time) error {
	changes := make(map[string][2]string)
	for field, value := range after {
		old, ok := before[field]
		if !ok {
			old = "None"
		}
		if old != value || !ok {
			changes[field] = [2]string{old, value}
		}
	}
	if len(changes) == 0 {
		return nil
	}
	action := 1
	if before == nil {
		action = 0
	}
	path := "direct"
	return appendModelAuditForApp(ctx, t.tx, "communications", modelAuditEntry{Model: "messagetemplate", Key: id,
		Representation: "MessageTemplate object (" + id + ")", Action: action, Changes: changes,
		Context: audit, AssociationID: &association, ResolutionPath: &path, At: at})
}

func (t *messageTemplateTransaction) Recipient(ctx context.Context, association, id string, group *string) (domain.MessageRecipient, error) {
	var row domain.MessageRecipient
	err := t.tx.GetContext(ctx, &row, `SELECT COALESCE(first_name,'') AS first_name,COALESCE(last_name,'') AS last_name,
COALESCE(email,'') AS email,COALESCE(phone,'') AS phone FROM application_associate
WHERE sport_association_id=$1 AND associate_id=$2 AND NOT deleted AND ($3::uuid IS NULL OR group_id=$3)`, association, id, group)
	if errors.Is(err, sql.ErrNoRows) {
		err = domain.ErrMessageTemplateNotFound
	}
	return row, err
}

func (t *messageTemplateTransaction) InsertOutgoingMessage(ctx context.Context, association, actor string, row domain.OutgoingMessage, at time.Time) error {
	_, err := t.tx.ExecContext(ctx, `INSERT INTO communications_outgoingmessage
(id,sport_association_id,actor_id,recipient_id,template_id,channel,locale,recipient,rendered_subject,rendered_body,
subject,body,action,status,created_at,updated_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$9,$10,'','preview',$11,$11)`,
		row.ID, association, actor, row.RecipientID, row.TemplateID, row.Channel, row.Locale, row.Recipient, row.Subject, row.Body, at)
	return err
}

func (t *messageTemplateTransaction) LockOutgoingMessage(ctx context.Context, association, actor, id string) (domain.OutgoingMessage, error) {
	var row domain.OutgoingMessage
	err := t.tx.GetContext(ctx, &row, `SELECT id,recipient_id,template_id,channel,locale,recipient,subject,body,action,status,updated_at
FROM communications_outgoingmessage WHERE sport_association_id=$1 AND actor_id=$2 AND id=$3 FOR UPDATE`, association, actor, id)
	if errors.Is(err, sql.ErrNoRows) {
		err = domain.ErrMessageTemplateNotFound
	}
	return row, err
}

// SaveOutgoingMessageAction updates a preview already held by LockOutgoingMessage.
func (t *messageTemplateTransaction) SaveOutgoingMessageAction(ctx context.Context, association string, row domain.OutgoingMessage, at time.Time) error {
	_, err := t.tx.ExecContext(ctx, `UPDATE communications_outgoingmessage SET subject=$3,body=$4,action=$5,status=$6,updated_at=$7
WHERE sport_association_id=$1 AND id=$2`, association, row.ID, row.Subject, row.Body, row.Action, row.Status, at)
	return err
}

func (r *MessageTemplates) FinishOutgoingMessage(ctx context.Context, id, association, status string, at time.Time) error {
	tx, err := beginOwnedTransaction(ctx, r.db, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err := tx.tx.ExecContext(ctx, `UPDATE communications_outgoingmessage SET status=$3,updated_at=$4
WHERE sport_association_id=$1 AND id=$2 AND status IN ('sending', 'unknown')`, association, id, status, at); err != nil {
		return err
	}
	return tx.Commit()
}
