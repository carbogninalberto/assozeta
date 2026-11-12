package service

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

type PaymentCategories struct {
	store domain.PaymentCategoryStore
	now   func() time.Time
}

func NewPaymentCategories(store domain.PaymentCategoryStore, now func() time.Time) *PaymentCategories {
	return &PaymentCategories{store: store, now: now}
}

// AdmitAdd reserves the same role/active-association checks as the mutation
// before the transport parses a body. The mutation repeats them transactionally.
func (s *PaymentCategories) AdmitAdd(ctx context.Context, identity domain.ResolvedIdentity) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	owner, err := paymentCategoryOwner(identity, domain.ErrPaymentCategoryForbidden)
	if err != nil {
		return err
	}
	tx, err := s.store.BeginPaymentCategories(ctx)
	if err != nil {
		return err
	}
	if err = lockPaymentCategoryOwner(ctx, tx, owner); err != nil {
		_ = tx.Rollback()
		return err
	}
	if _, err = tx.AssociationForOwner(ctx, owner, paymentCategoryAssociationScope(identity)); err != nil {
		_ = tx.Rollback()
		return err
	}
	return tx.Rollback()
}

// AdmitUpdate reproduces the view's role, association and target lookup order
// before serializer validation. The mutation acquires the lock again.
func (s *PaymentCategories) AdmitUpdate(ctx context.Context, identity domain.ResolvedIdentity, id string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	owner, err := paymentCategoryOwner(identity, domain.ErrPaymentCategoryForbidden)
	if err != nil {
		return err
	}
	tx, err := s.store.BeginPaymentCategories(ctx)
	if err != nil {
		return err
	}
	if err = lockPaymentCategoryOwner(ctx, tx, owner); err != nil {
		_ = tx.Rollback()
		return err
	}
	association, err := tx.AssociationForOwner(ctx, owner, paymentCategoryAssociationScope(identity))
	if err != nil {
		_ = tx.Rollback()
		return err
	}
	parsedID, err := uuid.Parse(id)
	if err != nil {
		_ = tx.Rollback()
		return domain.ErrPaymentCategoryInvalidID
	}
	category, err := tx.CategoryForUpdate(ctx, parsedID.String(), association)
	if err != nil {
		_ = tx.Rollback()
		return err
	}
	if category.ID != parsedID.String() || category.AssociationID == nil || *category.AssociationID != association {
		_ = tx.Rollback()
		return domain.ErrPaymentCategoryNotFound
	}
	return tx.Rollback()
}

func (s *PaymentCategories) List(ctx context.Context, identity domain.ResolvedIdentity) ([]dto.PaymentCategory, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	owner, err := paymentCategoryOwner(identity, domain.ErrPaymentCategoryListForbidden)
	if err != nil {
		return nil, err
	}
	rows, err := s.store.ListPaymentCategories(ctx, owner, paymentCategoryAssociationScope(identity))
	if err != nil {
		return nil, err
	}
	result := make([]dto.PaymentCategory, 0, len(rows))
	for _, row := range rows {
		result = append(result, dto.PaymentCategoryFromState(row))
	}
	return result, nil
}

func (s *PaymentCategories) Add(ctx context.Context, identity domain.ResolvedIdentity, input dto.PaymentCategoryWrite, audit domain.LoginAuditContext) (dto.PaymentCategory, error) {
	empty := dto.PaymentCategory{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	owner, err := paymentCategoryOwner(identity, domain.ErrPaymentCategoryForbidden)
	if err != nil {
		return empty, err
	}
	audit = paymentCategoryAuditContext(identity, audit)
	tx, err := s.store.BeginPaymentCategories(ctx)
	if err != nil {
		return empty, err
	}
	defer tx.Rollback()
	if err = lockPaymentCategoryOwner(ctx, tx, owner); err != nil {
		return empty, err
	}
	association, err := tx.AssociationForOwner(ctx, owner, paymentCategoryAssociationScope(identity))
	if err != nil {
		return empty, err
	}
	if err = validatePaymentCategoryWrite(ctx, tx, input, nil); err != nil {
		return empty, err
	}
	at := s.now().UTC().Truncate(time.Microsecond)
	state := domain.PaymentCategoryState{
		Name: *input.Name, CreationDate: at, AssociationID: &association, Type: 1,
	}
	if input.CategoryID != nil {
		state.ID = *input.CategoryID

	} else {
		state.ID = uuid.NewString()
	}
	if input.CreationDate != nil {
		state.CreationDate = input.CreationDate.UTC()
	}
	if input.Expense != nil {
		state.Expense = *input.Expense
	}
	if input.TaxDeductible != nil {
		state.TaxDeductible = *input.TaxDeductible
	}
	if input.Archived != nil {
		state.Archived = *input.Archived
	}
	if input.Type != nil {
		state.Type = *input.Type
	}
	if input.Deleted != nil {
		state.Deleted = *input.Deleted
	}
	vat, created, err := s.ensureVAT(ctx, tx, input.VAT.Value)
	if err != nil {
		return empty, err
	}
	state.VAT = vat
	if err = tx.InsertPaymentCategory(ctx, state); err != nil {
		return empty, err
	}
	if created {
		event := domain.PaymentCategoryAudit{
			Model: "vatmanagement", ID: vat.ID, ObjectRepresentation: paymentCategoryVATRepresentation(vat.ID), Action: 0,
			Changes: paymentCategoryVATCreateChanges(vat), Context: audit, At: at,
		}
		if err = s.attachVATAuditAssociation(ctx, tx, identity, association, &event); err != nil {
			return empty, err
		}
		if err = tx.AppendPaymentCategoryAudit(ctx, event); err != nil {
			return empty, err
		}
	}
	event := domain.PaymentCategoryAudit{
		Model: "paymentcategory", ID: state.ID, ObjectRepresentation: paymentCategoryRepresentation(state.ID), Action: 0,
		Changes: paymentCategoryCreateChanges(state), Context: audit, AssociationID: &association, ResolutionPath: stringPointer("direct"), At: at,
	}
	if err = tx.AppendPaymentCategoryAudit(ctx, event); err != nil {
		return empty, err
	}
	if err = ctx.Err(); err != nil {
		return empty, err
	}
	if err = tx.Commit(); err != nil {
		return empty, err
	}
	return dto.PaymentCategoryFromState(state), nil
}

func (s *PaymentCategories) Update(ctx context.Context, identity domain.ResolvedIdentity, id string, input dto.PaymentCategoryWrite, audit domain.LoginAuditContext) (dto.PaymentCategory, error) {
	empty := dto.PaymentCategory{}
	if err := ctx.Err(); err != nil {
		return empty, err
	}
	owner, err := paymentCategoryOwner(identity, domain.ErrPaymentCategoryForbidden)
	if err != nil {
		return empty, err
	}
	audit = paymentCategoryAuditContext(identity, audit)
	tx, err := s.store.BeginPaymentCategories(ctx)
	if err != nil {
		return empty, err
	}
	defer tx.Rollback()
	if err = lockPaymentCategoryOwner(ctx, tx, owner); err != nil {
		return empty, err
	}
	association, err := tx.AssociationForOwner(ctx, owner, paymentCategoryAssociationScope(identity))
	if err != nil {
		return empty, err
	}
	parsedID, err := uuid.Parse(id)
	if err != nil {
		return empty, domain.ErrPaymentCategoryInvalidID
	}
	id = parsedID.String()
	before, err := tx.CategoryForUpdate(ctx, id, association)
	if err != nil {
		return empty, err
	}
	if before.ID != id || before.AssociationID == nil || *before.AssociationID != association {
		return empty, domain.ErrPaymentCategoryNotFound
	}
	if err = validatePaymentCategoryWrite(ctx, tx, input, &before); err != nil {
		return empty, err
	}
	after := before
	applyPaymentCategoryWrite(&after, input)
	at := s.now().UTC().Truncate(time.Microsecond)
	if input.VAT.Present {
		if input.VAT.Null {
			return empty, &dto.PaymentCategoryValidationError{Errors: map[string]any{
				"vat_management": []string{dto.PaymentCategoryValidationNull},
			}}
		} else if input.VAT.Value.NonEmpty {
			vat, created, err := s.ensureVAT(ctx, tx, input.VAT.Value)
			if err != nil {
				return empty, err
			}
			after.VAT = vat
			if created {
				event := domain.PaymentCategoryAudit{
					Model: "vatmanagement", ID: vat.ID, ObjectRepresentation: paymentCategoryVATRepresentation(vat.ID), Action: 0,
					Changes: paymentCategoryVATCreateChanges(vat), Context: audit, At: at,
				}
				if err = s.attachVATAuditAssociation(ctx, tx, identity, association, &event); err != nil {
					return empty, err
				}
				if err = tx.AppendPaymentCategoryAudit(ctx, event); err != nil {
					return empty, err
				}
			}
		}
	}
	if err = tx.UpdatePaymentCategory(ctx, after); err != nil {
		return empty, err
	}
	changes := paymentCategoryDifferences(before, after)
	if len(changes) > 0 {
		event := domain.PaymentCategoryAudit{
			Model: "paymentcategory", ID: after.ID, ObjectRepresentation: paymentCategoryRepresentation(after.ID), Action: 1,
			Changes: changes, Context: audit, AssociationID: &association, ResolutionPath: stringPointer("direct"), At: at,
		}
		if err = tx.AppendPaymentCategoryAudit(ctx, event); err != nil {
			return empty, err
		}
	}
	if err = ctx.Err(); err != nil {
		return empty, err
	}
	if err = tx.Commit(); err != nil {
		return empty, err
	}
	return dto.PaymentCategoryFromState(after), nil
}

func (s *PaymentCategories) Delete(ctx context.Context, identity domain.ResolvedIdentity, id string, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	owner, err := paymentCategoryOwner(identity, domain.ErrPaymentCategoryForbidden)
	if err != nil {
		return err
	}
	audit = paymentCategoryAuditContext(identity, audit)
	tx, err := s.store.BeginPaymentCategories(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = lockPaymentCategoryOwner(ctx, tx, owner); err != nil {
		return err
	}
	association, err := tx.AssociationForOwner(ctx, owner, paymentCategoryAssociationScope(identity))
	if err != nil {
		return err
	}
	parsedID, err := uuid.Parse(id)
	if err != nil {
		return domain.ErrPaymentCategoryInvalidID
	}
	id = parsedID.String()
	before, err := tx.CategoryForUpdate(ctx, id, association)
	if err != nil {
		return err
	}
	if before.ID != id || before.AssociationID == nil || *before.AssociationID != association {
		return domain.ErrPaymentCategoryNotFound
	}
	after := before
	after.Deleted = true
	if err = tx.UpdatePaymentCategory(ctx, after); err != nil {
		return err
	}
	if !before.Deleted {
		event := domain.PaymentCategoryAudit{
			Model: "paymentcategory", ID: after.ID, ObjectRepresentation: paymentCategoryRepresentation(after.ID), Action: 1,
			Changes: map[string][2]string{"deleted": {"False", "True"}}, Context: audit,
			AssociationID: &association, ResolutionPath: stringPointer("direct"), At: s.now().UTC().Truncate(time.Microsecond),
		}
		if err = tx.AppendPaymentCategoryAudit(ctx, event); err != nil {
			return err
		}
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *PaymentCategories) ensureVAT(ctx context.Context, tx domain.PaymentCategoryTransaction, input dto.PaymentCategoryVATInput) (*domain.PaymentCategoryVAT, bool, error) {
	if input.ID != nil {
		existing, err := tx.VATByID(ctx, *input.ID)
		if err != nil {
			return nil, false, err
		}
		if existing != nil {
			return nil, false, &dto.PaymentCategoryValidationError{Errors: map[string]any{
				"vat_management": map[string]any{"vat_management_id": []string{dto.PaymentCategoryValidationUniqueVAT}},
			}}
		}
	}
	value := domain.PaymentCategoryVAT{}
	if input.ID != nil {
		value.ID = *input.ID
	} else {
		value.ID = uuid.NewString()
	}
	if input.VAT != nil {
		value.VAT = *input.VAT
	}
	if input.Deduction != nil {
		value.Deduction = *input.Deduction
	}
	if err := tx.CreateVAT(ctx, value); err != nil {
		return nil, false, err
	}
	return &value, true, nil
}

func (s *PaymentCategories) attachVATAuditAssociation(ctx context.Context, tx domain.PaymentCategoryTransaction, identity domain.ResolvedIdentity, scopeAssociation string, event *domain.PaymentCategoryAudit) error {
	if identity.Actor == nil {
		return domain.ErrInvalidScopedRequest
	}
	association := scopeAssociation
	if identity.ScopeUser != nil && identity.Actor.ID == identity.ScopeUser.ID {
		event.AssociationID = &association
		event.ResolutionPath = stringPointer("actor.sport_association")
		return nil
	}
	actorAssociation, err := tx.AssociationForActor(ctx, identity.Actor.ID)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return nil
	}
	if err != nil {
		return err
	}
	event.AssociationID = &actorAssociation
	event.ResolutionPath = stringPointer("actor.sport_association")
	return nil
}

func paymentCategoryOwner(identity domain.ResolvedIdentity, forbidden error) (string, error) {
	if identity.Actor == nil || identity.Actor.ID == "" || identity.Effective == nil || identity.Effective.ID == "" ||
		identity.ScopeUser == nil || identity.ScopeUser.ID == "" {
		return "", domain.ErrInvalidScopedRequest
	}
	if identity.ScopeUser.Role != domain.RoleAssociation {
		return "", forbidden
	}
	if identity.Collaborator && (identity.Effective.Role != domain.RoleCollaborator || identity.Effective.ConnectedUserID == nil ||
		*identity.Effective.ConnectedUserID != identity.ScopeUser.ID) {
		return "", domain.ErrInvalidScopedRequest
	}
	return identity.ScopeUser.ID, nil
}

func lockPaymentCategoryOwner(ctx context.Context, tx domain.PaymentCategoryTransaction, owner string) error {
	role, active, deleted, err := tx.OwnerForUpdate(ctx, owner)
	if err != nil {
		return err
	}
	if role != domain.RoleAssociation || !active || deleted {
		return domain.ErrPaymentCategoryForbidden
	}
	return nil
}

func paymentCategoryAssociationScope(identity domain.ResolvedIdentity) *string {
	if identity.ImpersonationAssociationID == "" {
		return nil
	}
	value := identity.ImpersonationAssociationID
	return &value
}

func paymentCategoryAuditContext(identity domain.ResolvedIdentity, audit domain.LoginAuditContext) domain.LoginAuditContext {
	audit.ActorID = &identity.Actor.ID
	audit.ActorEmail = &identity.Actor.Email
	audit.AdditionalData = nil
	if identity.Impersonating {
		audit.AdditionalData = &domain.AuditImpersonation{
			SessionID: identity.SessionID, ActorID: identity.Actor.ID, TargetID: identity.Effective.ID,
			AssociationID: paymentCategoryAssociationScope(identity),
		}
	}
	return audit
}

func applyPaymentCategoryWrite(state *domain.PaymentCategoryState, input dto.PaymentCategoryWrite) {
	if input.Name != nil {
		state.Name = *input.Name
	}
	if input.CreationDate != nil {
		state.CreationDate = input.CreationDate.UTC()
	}
	if input.Expense != nil {
		state.Expense = *input.Expense
	}
	if input.TaxDeductible != nil {
		state.TaxDeductible = *input.TaxDeductible
	}
	if input.Archived != nil {
		state.Archived = *input.Archived
	}
	if input.Type != nil {
		state.Type = *input.Type
	}
	if input.Deleted != nil {
		state.Deleted = *input.Deleted
	}
}

func paymentCategoryCreateChanges(state domain.PaymentCategoryState) map[string][2]string {
	values := paymentCategoryAuditValues(state)
	changes := make(map[string][2]string, len(values))
	for name, value := range values {
		changes[name] = [2]string{"None", value}
	}
	// auditlog includes reverse concrete relations on CREATE. A new category
	// cannot already be referenced as a user's default, matching Django's
	// RelatedManager string representation.
	changes["users_default_category"] = [2]string{"None", "application.User.None"}
	changes["users_default_category_courses"] = [2]string{"None", "application.User.None"}
	return changes
}

func paymentCategoryDifferences(before, after domain.PaymentCategoryState) map[string][2]string {
	oldValues, newValues := paymentCategoryAuditValues(before), paymentCategoryAuditValues(after)
	changes := map[string][2]string{}
	for name, value := range newValues {
		if oldValues[name] != value {
			changes[name] = [2]string{oldValues[name], value}
		}
	}
	return changes
}

func paymentCategoryAuditValues(state domain.PaymentCategoryState) map[string]string {
	association, vat := "None", "None"
	if state.AssociationID != nil {
		association = *state.AssociationID
	}
	if state.VAT != nil {
		vat = state.VAT.ID
	}
	return map[string]string{
		"payment_category_id": state.ID, "name": state.Name,
		"creation_date": paymentCategoryAuditTime(state.CreationDate), "sport_association": association,
		"expense": paymentCategoryAuditBool(state.Expense), "tax_deductible": paymentCategoryAuditBool(state.TaxDeductible),
		"archived": paymentCategoryAuditBool(state.Archived), "type": paymentCategoryAuditInt(state.Type),
		"vat_management": vat, "deleted": paymentCategoryAuditBool(state.Deleted),
	}
}

func paymentCategoryVATCreateChanges(state *domain.PaymentCategoryVAT) map[string][2]string {
	return map[string][2]string{
		"vat_management_id": {"None", state.ID},
		"vat":               {"None", paymentCategoryAuditInt(state.VAT)},
		"deduction":         {"None", paymentCategoryAuditInt(state.Deduction)},
	}
}

func paymentCategoryAuditBool(value bool) string {
	if value {
		return "True"
	}
	return "False"
}

func paymentCategoryAuditInt(value uint16) string {
	return strconv.Itoa(int(value))
}

func paymentCategoryAuditTime(value time.Time) string {
	value = value.UTC()
	base := value.Format("2006-01-02 15:04:05")
	if microsecond := value.Nanosecond() / 1000; microsecond != 0 {
		return base + fmt.Sprintf(".%06d", microsecond)
	}
	return base
}

func paymentCategoryRepresentation(id string) string {
	return "PaymentCategory object (" + id + ")"
}

func paymentCategoryVATRepresentation(id string) string {
	return "VatManagement object (" + id + ")"
}

func stringPointer(value string) *string { return &value }
