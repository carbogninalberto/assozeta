package service

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// CampsCore ports the camps list, add, info, update and delete views over the
// camps periods request helpers.
type CampsCore struct {
	periods *CampsPeriods
	store   domain.CampsCoreStore
}

func NewCampsCore(store domain.CampsCoreStore, now func() time.Time, ids func() string) *CampsCore {
	return &CampsCore{periods: &CampsPeriods{now: now, ids: ids}, store: store}
}

type campsCoreRequest struct {
	*campsRequest
	core domain.CampsCoreTransaction
}

func (s *CampsCore) begin(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext) (*campsCoreRequest, error) {
	owner, ok := campsRequestUser(identity)
	if !ok {
		return nil, ErrCampsNotAssociation
	}
	tx, err := s.store.BeginCampsCore(ctx)
	if err != nil {
		return nil, err
	}
	return &campsCoreRequest{campsRequest: &campsRequest{s: s.periods, tx: tx, identity: identity, owner: owner,
		audit: paymentCategoryAuditContext(identity, audit), at: s.periods.now().UTC().Truncate(time.Microsecond)}, core: tx}, nil
}

// camp is the lookup of info, update and delete: the camp (404), then its
// association against request.user.sport_association.
func (r *campsCoreRequest) camp(ctx context.Context, id string, lock bool, denied string) (*domain.Camp, error) {
	row, err := r.core.Camp(ctx, id, lock)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, ErrCampsNotFound
	}
	association, err := r.association(ctx)
	if err != nil {
		return nil, err
	}
	if row.AssociationID != association {
		return nil, &CampsForbidden{Detail: denied}
	}
	return row, nil
}

// List follows camps_and_retreats_list: a user without an association is an
// unhandled DoesNotExist (the 500 page).
func (s *CampsCore) List(ctx context.Context, identity domain.ResolvedIdentity) ([]domain.Camp, error) {
	r, err := s.begin(ctx, identity, domain.LoginAuditContext{})
	if err != nil {
		return nil, err
	}
	defer r.tx.Rollback()
	association, err := r.tx.OwnerAssociation(ctx, r.owner)
	if err != nil {
		return nil, errors.Join(errors.New("camps list without an association"), err)
	}
	return r.core.Camps(ctx, association)
}

// CampInfo is CampsAndRetreatsInfoSerializer's data, or the public
// serializer's when Public is set.
type CampInfo struct {
	Camp    domain.Camp
	Public  *domain.CampAssociationPublic
	Periods []CampsPeriodInfo
}

func campPeriods(ctx context.Context, tx domain.CampsCoreTransaction, camp string) ([]CampsPeriodInfo, error) {
	rows, err := tx.CampPeriods(ctx, camp)
	if err != nil {
		return nil, err
	}
	result := make([]CampsPeriodInfo, 0, len(rows))
	for _, row := range rows {
		ids, err := tx.PeriodServiceIDs(ctx, row.ID)
		if err != nil {
			return nil, err
		}
		services, err := tx.PeriodServices(ctx, row.ID)
		if err != nil {
			return nil, err
		}
		result = append(result, CampsPeriodInfo{Period: CampsPeriodResult{Row: row, Fee: row.Fee, Services: ids}, Services: services})
	}
	return result, nil
}

// Info follows camps_and_retreats_info: is_valid_uuid first; users that are
// not associations or collaborators get the public serializer.
func (s *CampsCore) Info(ctx context.Context, identity domain.ResolvedIdentity, raw string) (*CampInfo, error) {
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return nil, err
	}
	if _, ok := campsRequestUser(identity); !ok {
		tx, err := s.store.BeginCampsCore(ctx)
		if err != nil {
			return nil, err
		}
		defer tx.Rollback()
		camp, err := tx.Camp(ctx, id, false)
		if err != nil {
			return nil, err
		}
		if camp == nil {
			return nil, ErrCampsNotFound
		}
		public, err := tx.AssociationPublic(ctx, camp.AssociationID)
		if err != nil {
			return nil, err
		}
		periods, err := campPeriods(ctx, tx, camp.ID)
		if err != nil {
			return nil, err
		}
		return &CampInfo{Camp: *camp, Public: &public, Periods: periods}, nil
	}
	r, err := s.begin(ctx, identity, domain.LoginAuditContext{})
	if err != nil {
		return nil, err
	}
	defer r.tx.Rollback()
	camp, err := r.camp(ctx, id, false, "You are not allowed to view this camp or retreat")
	if err != nil {
		return nil, err
	}
	periods, err := campPeriods(ctx, r.core, camp.ID)
	if err != nil {
		return nil, err
	}
	return &CampInfo{Camp: *camp, Periods: periods}, nil
}

func (r *campsCoreRequest) validateCamp(ctx context.Context, input *dto.CampInput, instance *domain.Camp) (*CampsValidationError, error) {
	errs := input.Errors
	// UniqueValidator excludes the instance.
	if input.ID.Present && (instance == nil || input.ID.ID != instance.ID) {
		taken, err := r.core.CampIDTaken(ctx, input.ID.ID)
		if err != nil {
			return nil, err
		}
		if taken {
			errs["camps_and_retreats_id"] = []string{"unique:camps and retreats:camps and retreats id"}
		}
	}
	if input.Association.Present {
		code, err := r.relation(ctx, input.Association, r.core.AssociationRelated)
		if err != nil {
			return nil, err
		}
		if code != "" {
			errs["sport_association"] = []string{code}
		}
	}
	return campsOrdered(errs, dto.CampFieldOrder), nil
}

// campCreatedChanges is auditlog's diff of a camp against nothing.
func campCreatedChanges(row domain.Camp) map[string][2]string {
	changes := map[string][2]string{"camps_and_retreats_id": {"None", row.ID}, "sport_association": {"None", row.AssociationID}}
	if row.Title != nil {
		changes["title"] = [2]string{"None", *row.Title}
	}
	if row.Description != nil {
		changes["description"] = [2]string{"None", *row.Description}
	}
	return changes
}

func applyCamp(row *domain.Camp, input *dto.CampInput) {
	if input.ID.Present {
		row.ID = input.ID.ID
	}
	if input.Title.Present {
		row.Title = input.Title.Value
	}
	if input.Description.Present {
		row.Description = input.Description.Value
	}
	if input.Association.Present {
		row.AssociationID = input.Association.ID
	}
}

func (r *campsCoreRequest) campAudit(row domain.Camp, action int, changes map[string][2]string) domain.CampsAudit {
	association := row.AssociationID
	return domain.CampsAudit{Model: "CampsAndRetreats", Key: row.ID, Action: action, Changes: changes, AssociationID: &association,
		Path: "direct"}
}

// Add follows camps_and_retreats_add: the association lookup, the assignment
// into request.data, the serializer save and the view's second save (no
// changes, no entry).
func (s *CampsCore) Add(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool,
	audit domain.LoginAuditContext) (*domain.Camp, error) {
	r, err := s.begin(ctx, identity, audit)
	if err != nil {
		return nil, err
	}
	defer r.tx.Rollback()
	association, err := r.association(ctx)
	if err != nil {
		return nil, err
	}
	object, err := dto.CampAssignAssociation(body, form, association)
	if err != nil {
		return nil, err
	}
	input, invalid, err := dto.ParseCampWrite(object, false, false)
	if err != nil {
		return nil, err
	}
	if invalid != nil {
		return nil, campsOrdered(invalid, []string{"non_field_errors"})
	}
	failure, err := r.validateCamp(ctx, input, nil)
	if err != nil {
		return nil, err
	}
	if failure != nil {
		return nil, failure
	}
	row := domain.Camp{ID: s.periods.ids()}
	applyCamp(&row, input)
	if err = r.core.InsertCamp(ctx, row); err != nil {
		return nil, err
	}
	if err = r.emit(ctx, r.campAudit(row, 0, campCreatedChanges(row))); err != nil {
		return nil, err
	}
	return &row, r.tx.Commit()
}

// Update follows camps_and_retreats_update (partial); the tenant scope's
// PermissionDenied on sport_association is re-raised.
func (s *CampsCore) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool,
	audit domain.LoginAuditContext) (*domain.Camp, error) {
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return nil, err
	}
	r, err := s.begin(ctx, identity, audit)
	if err != nil {
		return nil, err
	}
	defer r.tx.Rollback()
	old, err := r.camp(ctx, id, true, "You are not allowed to update this camp or retreat")
	if err != nil {
		return nil, err
	}
	input, invalid, err := dto.ParseCampWrite(body, form, true)
	if err != nil {
		return nil, err
	}
	if invalid != nil {
		return nil, campsOrdered(invalid, []string{"non_field_errors"})
	}
	failure, err := r.validateCamp(ctx, input, old)
	if err != nil {
		return nil, err
	}
	if failure != nil {
		return nil, failure
	}
	row := *old
	applyCamp(&row, input)
	if row.ID != old.ID {
		// Model.save() under a key no row has inserts a copy with the validated
		// values and the camp the path names stays. auditlog's pre_save finds no
		// row under the new key and logs the whole row as an update (indexed
		// from its changes), then post_save logs the create (indexed directly).
		if err = r.core.InsertCamp(ctx, row); err != nil {
			return nil, err
		}
		before := r.campAudit(row, 1, campCreatedChanges(row))
		before.Path = "changes_json.sport_association"
		if err = r.emit(ctx, before); err != nil {
			return nil, err
		}
		if err = r.emit(ctx, r.campAudit(row, 0, campCreatedChanges(row))); err != nil {
			return nil, err
		}
		return &row, r.tx.Commit()
	}
	if err = r.core.UpdateCamp(ctx, row); err != nil {
		return nil, err
	}
	changes := map[string][2]string{}
	diff := func(name, before, after string) {
		if before != after {
			changes[name] = [2]string{before, after}
		}
	}
	diff("title", campsText(old.Title), campsText(row.Title))
	diff("description", campsText(old.Description), campsText(row.Description))
	diff("sport_association", old.AssociationID, row.AssociationID)
	if len(changes) > 0 {
		if err = r.emit(ctx, r.campAudit(row, 1, changes)); err != nil {
			return nil, err
		}
	}
	return &row, r.tx.Commit()
}

// Delete follows camps_and_retreats_delete. The camp's entry resolves its
// index through changes_json.sport_association (the request user's active
// association), the cascaded ones through the actor's association.
func (s *CampsCore) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	r, err := s.begin(ctx, identity, audit)
	if err != nil {
		return err
	}
	defer r.tx.Rollback()
	camp, err := r.camp(ctx, id, true, "You are not allowed to delete this camp or retreat")
	if err != nil {
		return err
	}
	events, err := r.core.DeleteCamp(ctx, id)
	if err != nil {
		return err
	}
	for _, event := range events {
		if event.Model == "CampsAndRetreats" {
			association := camp.AssociationID
			event.AssociationID, event.Path = &association, "changes_json.sport_association"
		} else if event, err = r.actorIndexed(ctx, event); err != nil {
			return err
		}
		if err = r.emit(ctx, event); err != nil {
			return err
		}
	}
	return r.tx.Commit()
}
