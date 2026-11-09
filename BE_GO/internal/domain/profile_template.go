package domain

import (
	"context"
	"errors"
)

// ErrProfileTemplateSave is a database error of the association UPDATE; the
// transaction is restored to the savepoint before it, keeping the audit entry.
var ErrProfileTemplateSave = errors.New("profile template association save failed")

// ProfileTemplateAssociation is the owner's active association with the
// columns profile_update_subscription_template assigns, as stored.
type ProfileTemplateAssociation struct {
	ID string
	// Text and Decimal values are the column text (nil for NULL); JSON values
	// are the jsonb text (nil for SQL NULL).
	Text, JSON, Decimal map[string]*string
	Boolean             map[string]bool
}

// ProfileTemplateColumn is one converted column value: *string for text,
// jsonb text and numeric literals (nil for NULL), bool for booleans.
type ProfileTemplateColumn struct {
	Name  string
	Value any
}

type ProfileTemplateStore interface {
	BeginProfileTemplate(context.Context) (ProfileTemplateTransaction, error)
}

type ProfileTemplateTransaction interface {
	// Association locks SportAssociation.objects.get(user=owner), scoped to
	// association when it is not empty; nil when there is none.
	Association(ctx context.Context, owner, association string) (*ProfileTemplateAssociation, error)
	AppendAudit(context.Context, ProfileMiscAudit) error
	Save(ctx context.Context, id string, columns []ProfileTemplateColumn) error
	Commit() error
	Rollback() error
}

// ProfileTemplateImages is compress_base64 at the avatar (200x200) and the
// subscription-template logo (590x130) bounds.
type ProfileTemplateImages interface {
	CompressLoginImage(context.Context, string) (string, error)
	CompressTemplateLogo(context.Context, string) (string, error)
}
