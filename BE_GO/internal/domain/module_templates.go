package domain

import (
	"context"
	"errors"
	"time"
)

var ErrModuleTemplateNotFound = errors.New("No SportAssociationModuleTemplates matches the given query.")

type ModuleTemplate struct {
	ID                 string  `db:"sport_association_module_templates_id"`
	AssociationID      string  `db:"sport_association_id"`
	Name               string  `db:"name"`
	Template           *string `db:"template"`
	Footer             bool    `db:"footer"`
	Header             bool    `db:"header"`
	CustomFooterHeader bool    `db:"custom_footer_header"`
	CustomFooter       *string `db:"custom_footer"`
	CustomHeader       *string `db:"custom_header"`
}

type ModuleTemplateAudit struct {
	Template ModuleTemplate
	Action   int
	Context  LoginAuditContext
	At       time.Time
}

type ModuleTemplateStore interface {
	// ModuleTemplates lists one association's rows without ordering, like the view.
	ModuleTemplates(ctx context.Context, association string) ([]ModuleTemplate, error)
	ActiveAssociationForUser(ctx context.Context, user string) (string, error)
	BeginModuleTemplateMutations(context.Context) (ModuleTemplateTransaction, error)
}

type ModuleTemplateTransaction interface {
	AssociationForOwner(context.Context, string, *string) (string, error)
	ModuleTemplateIDExists(ctx context.Context, id string) (bool, error)
	InsertModuleTemplate(context.Context, ModuleTemplate) error
	// DeleteModuleTemplates locks, deletes and returns the association's rows among ids.
	DeleteModuleTemplates(ctx context.Context, association string, ids []string) ([]ModuleTemplate, error)
	AppendModuleTemplateAudit(context.Context, ModuleTemplateAudit) error
	Commit() error
	Rollback() error
}
