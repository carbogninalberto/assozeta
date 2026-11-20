package domain

import (
	"context"
	"errors"
	"time"
)

var ErrMessageTemplateNotFound = errors.New("message template resource not found")

type MessageTemplate struct {
	ID        string `db:"id" json:"id"`
	Channel   string `db:"channel" json:"channel"`
	Locale    string `db:"locale" json:"locale"`
	Name      string `db:"name" json:"name"`
	Subject   string `db:"subject" json:"subject"`
	Body      string `db:"body" json:"body"`
	Enabled   bool   `db:"enabled" json:"enabled"`
	IsDefault bool   `db:"is_default" json:"is_default"`
}

type MessageRecipient struct {
	FirstName string `db:"first_name"`
	LastName  string `db:"last_name"`
	Email     string `db:"email"`
	Phone     string `db:"phone"`
}

type OutgoingMessage struct {
	ID          string    `db:"id" json:"id"`
	RecipientID string    `db:"recipient_id" json:"-"`
	TemplateID  string    `db:"template_id" json:"template_id"`
	Channel     string    `db:"channel" json:"channel"`
	Locale      string    `db:"locale" json:"locale"`
	Recipient   string    `db:"recipient" json:"recipient"`
	Subject     string    `db:"subject" json:"subject"`
	Body        string    `db:"body" json:"body"`
	Action      string    `db:"action" json:"-"`
	Status      string    `db:"status" json:"status"`
	UpdatedAt   time.Time `db:"updated_at" json:"-"`
}

type MessageTemplateStore interface {
	BeginMessageTemplates(context.Context) (MessageTemplateTransaction, error)
	FinishOutgoingMessage(ctx context.Context, id, association, status string, at time.Time) error
}

type MessageTemplateTransaction interface {
	Association(ctx context.Context, owner string, scope *string, lock bool) (id, name string, err error)
	HasTemplates(ctx context.Context, association string) (bool, error)
	Templates(ctx context.Context, association string, enabledOnly bool) ([]MessageTemplate, error)
	InsertTemplate(ctx context.Context, association string, row MessageTemplate, audit LoginAuditContext, at time.Time) error
	UpdateTemplate(ctx context.Context, association string, before, row MessageTemplate, audit LoginAuditContext, at time.Time) error
	ClearDefault(ctx context.Context, association string, row MessageTemplate, audit LoginAuditContext, at time.Time) error
	ArchiveTemplate(ctx context.Context, association, id string, audit LoginAuditContext, at time.Time) error
	Recipient(ctx context.Context, association, id string, group *string) (MessageRecipient, error)
	InsertOutgoingMessage(ctx context.Context, association, actor string, row OutgoingMessage, at time.Time) error
	LockOutgoingMessage(ctx context.Context, association, actor, id string) (OutgoingMessage, error)
	SaveOutgoingMessageAction(ctx context.Context, association string, row OutgoingMessage, at time.Time) error
	Commit() error
	Rollback() error
}
