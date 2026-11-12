package service

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

type MessageMutations struct {
	store domain.MessageMutationStore
	now   func() time.Time
}

func NewMessageMutations(store domain.MessageMutationStore, now func() time.Time) *MessageMutations {
	return &MessageMutations{store: store, now: now}
}

// Admit precedes OPTIONS/method dispatch and body parsing, like MessageReads.
func (s *MessageMutations) Admit(ctx context.Context, identity domain.ResolvedIdentity) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	_, err := messageMutationOwner(identity)
	return err
}
func messageMutationOwner(identity domain.ResolvedIdentity) (string, error) {
	return paymentCategoryOwner(identity, domain.ErrMessageMutationForbidden)
}
func messageMutationLockOwner(ctx context.Context, tx domain.MessageMutationTransaction, owner string) error {
	role, active, deleted, err := tx.OwnerForUpdate(ctx, owner)
	if err != nil {
		return err
	}
	if role != domain.RoleAssociation || !active || deleted {
		return domain.ErrMessageMutationForbidden
	}
	return nil
}
func (s *MessageMutations) Add(ctx context.Context, identity domain.ResolvedIdentity, input dto.MessageMutationWrite, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	owner, err := messageMutationOwner(identity)
	if err != nil {
		return err
	}
	// Revalidate service inputs: non-HTTP callers cannot bypass the serializer.
	object := domain.RequestObject{{Name: "type", Value: input.Type}, {Name: "message", Value: input.Message}}
	if input.Subject != nil {
		object = append(object, domain.RequestField{Name: "subject", Value: *input.Subject})
	}
	if _, err = dto.ParseMessageMutationWrite(object); err != nil {
		return err
	}
	tx, err := s.store.BeginMessageMutations(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = messageMutationLockOwner(ctx, tx, owner); err != nil {
		return err
	}
	association, err := tx.AssociationForOwner(ctx, owner, paymentCategoryAssociationScope(identity))
	if err != nil {
		return err
	}
	at := s.now().UTC().Truncate(time.Microsecond)
	state := domain.MessageMutationState{ID: uuid.NewString(), AssociationID: association, Type: input.Type, Message: input.Message, Subject: input.Subject, CreatedAt: &at}
	if err = tx.InsertMessage(ctx, state); err != nil {
		return err
	}
	event := messageMutationAudit(state, 0, at, paymentCategoryAuditContext(identity, audit))
	if err = tx.AppendMessageAudit(ctx, event); err != nil {
		return err
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}
func (s *MessageMutations) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	owner, err := messageMutationOwner(identity)
	if err != nil {
		return err
	}
	// Django validates the URL UUID before dereferencing sport_association.
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	tx, err := s.store.BeginMessageMutations(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err = messageMutationLockOwner(ctx, tx, owner); err != nil {
		return err
	}
	association, err := tx.AssociationForOwner(ctx, owner, paymentCategoryAssociationScope(identity))
	if err != nil {
		return err
	}
	state, err := tx.MessageForUpdate(ctx, id, association)
	if err != nil {
		return err
	}
	if state.ID != id || state.AssociationID != association {
		return domain.ErrMessageMutationNotFound
	}
	// PostgreSQL Django FKs need not use ON DELETE CASCADE. Delete the actual
	// reverse table explicitly while the parent row lock excludes FK writers.
	if err = tx.DeleteMessageDeliveries(ctx, id); err != nil {
		return err
	}
	if err = tx.DeleteMessage(ctx, id, association); err != nil {
		return err
	}
	if err = tx.AppendMessageAudit(ctx, messageMutationAudit(state, 2, s.now().UTC().Truncate(time.Microsecond), paymentCategoryAuditContext(identity, audit))); err != nil {
		return err
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}
func messageMutationAudit(state domain.MessageMutationState, action int, at time.Time, audit domain.LoginAuditContext) domain.MessageMutationAudit {
	values := map[string]string{"message_id": state.ID, "sport_association": state.AssociationID, "type": state.Type, "message": state.Message, "created_at": "None"}
	if state.Subject != nil {
		values["subject"] = *state.Subject
	}
	if state.CreatedAt != nil {
		values["created_at"] = paymentCategoryAuditTime(*state.CreatedAt)
	}
	changes := make(map[string][2]string, len(values))
	for key, value := range values {
		// auditlog compares text fields after stringification. The datetime
		// branch compares native None before stringifying the final pair.
		if key != "created_at" && value == "None" {
			continue
		}
		if action == 0 {
			changes[key] = [2]string{"None", value}
		} else {
			changes[key] = [2]string{value, "None"}
		}
	}
	path := "direct"
	if action == 2 {
		path = "changes_json.sport_association"
	}
	return domain.MessageMutationAudit{ID: state.ID, Action: action, Changes: changes, Context: audit, AssociationID: state.AssociationID, ResolutionPath: path, At: at}
}
