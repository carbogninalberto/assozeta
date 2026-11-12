package service

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// ErrDashboardLayoutBody is request.data.get on a non-dict body (500).
var ErrDashboardLayoutBody = errors.New("dashboard layout body has no get")

type DashboardLayouts struct {
	store domain.DashboardLayoutStore
	now   func() time.Time
}

func NewDashboardLayouts(store domain.DashboardLayoutStore, now func() time.Time) *DashboardLayouts {
	return &DashboardLayouts{store: store, now: now}
}

// Set follows statistic_dashboard_layout: the impersonation-scoped
// association get (DoesNotExist, 500), request.data.get, then save() of the
// acting collaborator's or the scope user's layout. auditlog renders JSON as
// sorted json.dumps and logs on pre_save, so the entry survives a value the
// database then rejects.
func (s *DashboardLayouts) Set(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	tx, err := s.store.BeginDashboardLayout(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.AssociationForOwner(ctx, identity.ScopeUser.ID, paymentCategoryAssociationScope(identity)); err != nil {
		return err
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return ErrDashboardLayoutBody
	}
	layout, _ := object.Lookup("dashboard_layout")
	user := identity.ScopeUser.ID
	if identity.Collaborator && identity.Effective != nil {
		user = identity.Effective.ID
	}
	owner, err := tx.DashboardLayoutForUpdate(ctx, user)
	if err != nil {
		return err
	}
	old := "null"
	if owner.Layout != nil {
		stored, err := dto.ParseProfileTablesJSON([]byte(*owner.Layout))
		if err != nil {
			return err
		}
		old = string(dto.PythonJSONDumpsSorted(stored))
	}
	if updated := string(dto.PythonJSONDumpsSorted(layout)); updated != old {
		event := domain.DashboardLayoutAudit{UserID: user, Representation: "name: " + owner.FirstName + " " + owner.LastName,
			Changes: map[string][2]string{"dashboard_layout": {old, updated}}, Context: paymentCategoryAuditContext(identity, audit),
			At: s.now().UTC().Truncate(time.Microsecond)}
		// User resolver first, then the actor's association; otherwise no index row.
		path := "user.sport_association"
		association, err := tx.ActiveAssociationOf(ctx, user)
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
		if err = tx.AppendDashboardLayoutAudit(ctx, event); err != nil {
			return err
		}
	}
	var stored *string
	if layout != nil {
		text := string(dto.PythonJSONDumps(layout))
		stored = &text
	}
	err = tx.SetDashboardLayout(ctx, user, stored)
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
