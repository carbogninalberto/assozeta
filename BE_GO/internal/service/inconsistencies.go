package service

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type Inconsistencies struct {
	store    domain.InconsistencyStore
	now      func() time.Time
	location *time.Location
}

func NewInconsistencies(store domain.InconsistencyStore, now func() time.Time, location *time.Location) *Inconsistencies {
	return &Inconsistencies{store: store, now: now, location: location}
}

// Check follows check_inconsistencies: the scope user's associates through
// the group-aware soft-delete manager (an X-Group-Id the UUID field rejects
// is logged and ignored), Associate.main_tutor for each (which promotes and
// saves the first relation when none is primary), then minors by
// date.today() without a main tutor.
func (s *Inconsistencies) Check(ctx context.Context, identity domain.ResolvedIdentity, groupHeader string, audit domain.LoginAuditContext) (dto.InconsistencyReport, error) {
	if err := ctx.Err(); err != nil {
		return dto.InconsistencyReport{}, err
	}
	var group *string
	if groupHeader != "" {
		if id, err := paymentInfoParseUUID(groupHeader); err == nil {
			text := id.String()
			group = &text
		}
	}
	tx, err := s.store.BeginInconsistencies(ctx)
	if err != nil {
		return dto.InconsistencyReport{}, err
	}
	defer tx.Rollback()
	associates, err := tx.Associates(ctx, identity.ScopeUser.ID, group, paymentCategoryAssociationScope(identity))
	if err != nil {
		return dto.InconsistencyReport{}, err
	}
	now := s.now().In(s.location)
	today := time.Date(now.Year(), now.Month(), now.Day(), 0, 0, 0, 0, time.UTC)
	report := dto.InconsistencyReport{MissingTutors: []dto.InconsistencyAssociate{}}
	for _, associate := range associates {
		hasTutor := associate.HasPrimary
		if !hasTutor && associate.FirstRelation != nil {
			if err = s.promote(ctx, tx, identity, associate, audit); err != nil {
				return dto.InconsistencyReport{}, err
			}
			hasTutor = true
		}
		if hasTutor || associate.BornDate == nil || dto.RelativeYears(today, *associate.BornDate) >= 18 {
			continue
		}
		report.MissingTutors = append(report.MissingTutors, dto.InconsistencyAssociateFrom(associate))
	}
	report.Inconsistencies = len(report.MissingTutors) > 0
	if err = ctx.Err(); err != nil {
		return dto.InconsistencyReport{}, err
	}
	return report, tx.Commit()
}

// promote is get_main_tutor's save, audited through the associate's
// association (or, without one, the actor's).
func (s *Inconsistencies) promote(ctx context.Context, tx domain.InconsistencyTransaction, identity domain.ResolvedIdentity, associate domain.InconsistencyAssociate, audit domain.LoginAuditContext) error {
	if err := tx.PromoteTutorRelation(ctx, *associate.FirstRelation); err != nil {
		return err
	}
	event := domain.TutorPromotionAudit{RelationID: *associate.FirstRelation, Context: paymentCategoryAuditContext(identity, audit),
		At: s.now().UTC().Truncate(time.Microsecond)}
	path := "associate.sport_association"
	association := associate.AssociationID
	if association == nil {
		var err error
		if association, err = tx.ActiveAssociationOf(ctx, identity.Actor.ID); err != nil {
			return err
		}
		path = "actor.sport_association"
	}
	if association != nil {
		event.AssociationID, event.Path = association, &path
	}
	return tx.AppendTutorPromotionAudit(ctx, event)
}
