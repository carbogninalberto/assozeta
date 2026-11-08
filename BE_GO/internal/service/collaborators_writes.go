package service

import (
	"context"
	"errors"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// ErrCollaboratorUpdateServer stands for the view's uncaught exceptions
// before any write: data.get on a non-dict body and a role int() rejects.
var ErrCollaboratorUpdateServer = errors.New("collaborator update raised")

type CollaboratorUpdates struct {
	store domain.CollaboratorUpdateStore
	now   func() time.Time
}

func NewCollaboratorUpdates(store domain.CollaboratorUpdateStore, now func() time.Time) *CollaboratorUpdates {
	return &CollaboratorUpdates{store: store, now: now}
}

// Update follows collaborators_update after request.data was parsed:
// is_valid_uuid, the live user connected to request.user (the owner for
// collaborators), int() of the role with its FULL fallback, the permissions
// kept only for the custom role, then save(). auditlog logs on pre_save, so
// its entry survives a value PostgreSQL then rejects.
func (s *CollaboratorUpdates) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	parsed, err := paymentInfoParseUUID(raw)
	if err != nil {
		return err
	}
	id := parsed.String()
	tx, err := s.store.BeginCollaboratorUpdate(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	target, err := tx.ConnectedCollaboratorForUpdate(ctx, identity.ScopeUser.ID, id)
	if err != nil {
		return err
	}
	if target == nil {
		return domain.ErrCollaboratorNotFound
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return ErrCollaboratorUpdateServer
	}
	role, ok := dto.CollaboratorRole(object.Lookup("collaborator_role"))
	if !ok {
		return ErrCollaboratorUpdateServer
	}
	var permissions any
	if role == 3 {
		permissions, _ = object.Lookup("collaborator_permissions")
	}
	changes := map[string][2]string{}
	if old, updated := strconv.FormatInt(target.Role, 10), strconv.FormatInt(role, 10); old != updated {
		changes["collaborator_role"] = [2]string{old, updated}
	}
	old := "null"
	if target.Permissions != nil {
		stored, err := dto.ParseProfileTablesJSON([]byte(*target.Permissions))
		if err != nil {
			return err
		}
		old = string(dto.PythonJSONDumpsSorted(stored))
	}
	if updated := string(dto.PythonJSONDumpsSorted(permissions)); updated != old {
		changes["collaborator_permissions"] = [2]string{old, updated}
	}
	if len(changes) > 0 {
		event := domain.DashboardLayoutAudit{UserID: id, Representation: "name: " + target.FirstName + " " + target.LastName,
			Changes: changes, Context: paymentCategoryAuditContext(identity, audit), At: s.now().UTC().Truncate(time.Microsecond)}
		// User resolver first, then the actor's association; otherwise no index row.
		path := "user.sport_association"
		association, err := tx.ActiveAssociationOf(ctx, id)
		if err == nil && association == nil {
			path = "actor.sport_association"
			association, err = tx.ActiveAssociationOf(ctx, identity.Actor.ID)
		}
		if err != nil {
			return err
		}
		if association != nil {
			event.AssociationID, event.ResolutionPath = association, &path
		}
		if err = tx.AppendCollaboratorAudit(ctx, event); err != nil {
			return err
		}
	}
	var stored *string
	if permissions != nil {
		text := string(dto.PythonJSONDumps(permissions))
		stored = &text
	}
	err = tx.SetCollaboratorRole(ctx, id, role, stored)
	var rejected *domain.DashboardLayoutRejected
	if errors.As(err, &rejected) {
		if commit := tx.Commit(); commit != nil {
			return commit
		}
		return err
	}
	if err != nil {
		return err
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	return tx.Commit()
}
