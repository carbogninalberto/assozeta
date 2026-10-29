package service

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

// ErrModuleTemplateLookup is DRF's bare Http404 ("Not found.") for a pk that
// Django's UUIDField cannot parse; a valid unknown pk is the model 404.
var ErrModuleTemplateLookup = errors.New("Not found.")

// ModuleTemplateValidation carries DRF validation codes for a 400.
type ModuleTemplateValidation struct{ Errors map[string]any }

func (e *ModuleTemplateValidation) Error() string { return "invalid module template" }

// ModuleTemplates follows SportAssociationModuleTemplatesViewSet: every
// action scopes by SportAssociation.objects.get(user=request.user), the
// identity's scope user, without the impersonation boundary.
type ModuleTemplates struct {
	store domain.ModuleTemplateStore
	now   func() time.Time
}

func NewModuleTemplates(store domain.ModuleTemplateStore, now func() time.Time) *ModuleTemplates {
	return &ModuleTemplates{store: store, now: now}
}

func (s *ModuleTemplates) List(ctx context.Context, identity domain.ResolvedIdentity) ([]dto.ModuleTemplateRow, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	association, err := s.store.ActiveAssociationForUser(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	rows, err := s.store.ModuleTemplates(ctx, association)
	if err != nil {
		return nil, err
	}
	result := make([]dto.ModuleTemplateRow, 0, len(rows))
	for _, row := range rows {
		result = append(result, dto.ModuleTemplateRowFrom(row))
	}
	return result, nil
}

func (s *ModuleTemplates) begin(ctx context.Context, identity domain.ResolvedIdentity) (domain.ModuleTemplateTransaction, string, error) {
	tx, err := s.store.BeginModuleTemplateMutations(ctx)
	if err != nil {
		return nil, "", err
	}
	association, err := tx.AssociationForOwner(ctx, identity.ScopeUser.ID, nil)
	if err != nil {
		_ = tx.Rollback()
		return nil, "", err
	}
	return tx, association, nil
}

func (s *ModuleTemplates) finish(ctx context.Context, tx domain.ModuleTemplateTransaction) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *ModuleTemplates) Add(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool, audit domain.LoginAuditContext) (dto.ModuleTemplateRow, error) {
	if err := ctx.Err(); err != nil {
		return dto.ModuleTemplateRow{}, err
	}
	tx, association, err := s.begin(ctx, identity)
	if err != nil {
		return dto.ModuleTemplateRow{}, err
	}
	defer tx.Rollback()
	input, err := dto.ParseModuleTemplateWrite(body, form, association)
	if err != nil {
		return dto.ModuleTemplateRow{}, err
	}
	if input.ID != nil {
		exists, err := tx.ModuleTemplateIDExists(ctx, *input.ID)
		if err != nil {
			return dto.ModuleTemplateRow{}, err
		}
		if exists {
			input.Errors["sport_association_module_templates_id"] = []string{dto.ModuleTemplateUniqueID}
		}
	}
	if len(input.Errors) > 0 {
		return dto.ModuleTemplateRow{}, &ModuleTemplateValidation{Errors: input.Errors}
	}
	row := input.Row
	row.ID = uuid.NewString()
	if input.ID != nil {
		row.ID = *input.ID
	}
	if err = tx.InsertModuleTemplate(ctx, row); err != nil {
		return dto.ModuleTemplateRow{}, err
	}
	if err = tx.AppendModuleTemplateAudit(ctx, domain.ModuleTemplateAudit{Template: row, Action: 0,
		Context: paymentCategoryAuditContext(identity, audit), At: s.now().UTC().Truncate(time.Microsecond)}); err != nil {
		return dto.ModuleTemplateRow{}, err
	}
	return dto.ModuleTemplateRowFrom(row), s.finish(ctx, tx)
}

// Delete is get_object (queryset scope, then the pk lookup) and delete().
func (s *ModuleTemplates) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	tx, association, err := s.begin(ctx, identity)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return ErrModuleTemplateLookup
	}
	rows, err := tx.DeleteModuleTemplates(ctx, association, []string{id.String()})
	if err != nil {
		return err
	}
	if len(rows) == 0 {
		return domain.ErrModuleTemplateNotFound
	}
	if err = s.audit(ctx, tx, identity, rows, audit); err != nil {
		return err
	}
	return s.finish(ctx, tx)
}

// BulkDelete deletes the association's rows among the requested IDs; others
// are ignored. Unparseable IDs raise in Django's lookup preparation (500).
func (s *ModuleTemplates) BulkDelete(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	raw, err := dto.ParseModuleTemplateIDs(body)
	if err != nil {
		return err
	}
	tx, association, err := s.begin(ctx, identity)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	ids := make([]string, 0, len(raw))
	for _, value := range raw {
		id, err := paymentInfoParseUUID(value)
		if err != nil {
			return dto.ErrModuleTemplateServer
		}
		ids = append(ids, id.String())
	}
	rows, err := tx.DeleteModuleTemplates(ctx, association, ids)
	if err != nil {
		return err
	}
	if err = s.audit(ctx, tx, identity, rows, audit); err != nil {
		return err
	}
	return s.finish(ctx, tx)
}

func (s *ModuleTemplates) audit(ctx context.Context, tx domain.ModuleTemplateTransaction, identity domain.ResolvedIdentity, rows []domain.ModuleTemplate, audit domain.LoginAuditContext) error {
	at := s.now().UTC().Truncate(time.Microsecond)
	for _, row := range rows {
		if err := tx.AppendModuleTemplateAudit(ctx, domain.ModuleTemplateAudit{Template: row, Action: 2,
			Context: paymentCategoryAuditContext(identity, audit), At: at}); err != nil {
			return err
		}
	}
	return nil
}
