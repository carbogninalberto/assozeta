package service

import (
	"context"
	"errors"
	"strconv"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

var (
	// ErrCampsNotAssociation is {'error': 'You are not a sport association.'}
	// (403); info answers its own message.
	ErrCampsNotAssociation = errors.New("camps user is not a sport association")
	// ErrCampsNotFound is {'error': 'Not found'} (404).
	ErrCampsNotFound = errors.New("camps row not found")
	// ErrCampsTenant is the tenant scope's PermissionDenied raised while a
	// related field validates.
	ErrCampsTenant = errors.New("camps relation of another tenant")
	// ErrCampsPrimaryKeyChange refuses a new primary key on update before any
	// write: Model.save() would insert a copy under the new key (not ported).
	ErrCampsPrimaryKeyChange = errors.New("camps primary key change is not ported")
	// ErrCampsPaymentNotPorted refuses a payment walk outcome this port does
	// not reproduce before any write.
	ErrCampsPaymentNotPorted = errors.New("camps payment walk is not ported")
)

// CampsForbidden is the view's PermissionDenied with its message (403).
type CampsForbidden struct{ Detail string }

func (e *CampsForbidden) Error() string { return e.Detail }

// CampsValidationError is serializer.errors in field order (400
// {'error': str(ValidationError)}).
type CampsValidationError struct{ Fields []CampsFieldErrors }

type CampsFieldErrors struct {
	Name  string
	Codes []string
}

func (e *CampsValidationError) Error() string { return "invalid camps input" }

// CampsSaveFailure is an exception raised after earlier writes were saved:
// the request is not atomic, so they are committed and the view answers
// 500 {'error': str(e)}.
type CampsSaveFailure struct{ Err *dto.CampsPythonError }

func (e *CampsSaveFailure) Error() string { return e.Err.Message }

type CampsPeriods struct {
	store domain.CampsStore
	now   func() time.Time
	ids   func() string
}

func NewCampsPeriods(store domain.CampsStore, now func() time.Time, ids func() string) *CampsPeriods {
	return &CampsPeriods{store: store, now: now, ids: ids}
}

// campsRequestUser is request.user: collaborators act as their owner.
func campsRequestUser(identity domain.ResolvedIdentity) (string, bool) {
	user := identity.ScopeUser
	if user.Role != domain.RoleAssociation && user.Role != domain.RoleCollaborator {
		return "", false
	}
	owner := user.ID
	if user.Role == domain.RoleCollaborator && user.ConnectedUserID != nil {
		owner = *user.ConnectedUserID
	}
	return owner, true
}

type campsRequest struct {
	s        *CampsPeriods
	tx       domain.CampsTransaction
	identity domain.ResolvedIdentity
	owner    string
	audit    domain.LoginAuditContext
	at       time.Time
	tenant   *string
	tenantOK bool
}

func (s *CampsPeriods) begin(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext) (*campsRequest, error) {
	owner, ok := campsRequestUser(identity)
	if !ok {
		return nil, ErrCampsNotAssociation
	}
	tx, err := s.store.BeginCamps(ctx)
	if err != nil {
		return nil, err
	}
	return &campsRequest{s: s, tx: tx, identity: identity, owner: owner, audit: paymentCategoryAuditContext(identity, audit),
		at: s.now().UTC().Truncate(time.Microsecond)}, nil
}

// association is request.user.sport_association (a miss is a 500 JSON).
func (r *campsRequest) association(ctx context.Context) (string, error) {
	association, err := r.tx.OwnerAssociation(ctx, r.owner)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return "", &CampsSaveFailure{Err: &dto.CampsPythonError{Message: "SportAssociation matching query does not exist."}}
	}
	return association, err
}

// belongs is tenant_scope.belongs_to_current_tenant for a resolved owner.
func (r *campsRequest) belongs(ctx context.Context, owner *string) (bool, error) {
	if !r.tenantOK {
		association, err := r.tx.OwnerAssociation(ctx, r.identity.ScopeUser.ID)
		if err != nil && !errors.Is(err, domain.ErrAssociationNotFound) {
			return false, err
		}
		if err == nil {
			r.tenant = &association
		}
		r.tenantOK = true
	}
	return r.tenant == nil || owner == nil || *owner == *r.tenant, nil
}

// relation is the scoped PrimaryKeyRelatedField lookup: a missing row is a
// field error, a row of another tenant raises PermissionDenied.
func (r *campsRequest) relation(ctx context.Context, input dto.CampsRelation, lookup func(context.Context, string) (domain.CampsRelated, error)) (string, error) {
	if input.Code != "" {
		return input.Code, nil
	}
	related, err := lookup(ctx, input.ID)
	if err != nil {
		return "", err
	}
	if !related.Found {
		return "related-does-not-exist:" + input.ID, nil
	}
	ok, err := r.belongs(ctx, related.AssociationID)
	if err != nil {
		return "", err
	}
	if !ok {
		return "", ErrCampsTenant
	}
	return "", nil
}

func (r *campsRequest) emit(ctx context.Context, events ...domain.CampsAudit) error {
	for _, event := range events {
		if err := r.tx.AppendAudit(ctx, event, r.audit, r.at); err != nil {
			return err
		}
	}
	return nil
}

// actorIndexed is a delete entry: the row is gone when the index resolves, so
// it falls back to the actor's association.
func (r *campsRequest) actorIndexed(ctx context.Context, event domain.CampsAudit) (domain.CampsAudit, error) {
	association, err := r.tx.ActorAssociation(ctx, r.identity.Actor.ID)
	if err != nil {
		return event, err
	}
	event.AssociationID, event.Path = association, "actor.sport_association"
	return event, nil
}

func campsOrdered(errors map[string][]string, order []string) *CampsValidationError {
	if len(errors) == 0 {
		return nil
	}
	result := &CampsValidationError{}
	for _, name := range order {
		if codes, ok := errors[name]; ok {
			result.Fields = append(result.Fields, CampsFieldErrors{Name: name, Codes: codes})
		}
	}
	return result
}

func campsText(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

func campsInt(value *int64) string {
	if value == nil {
		return "None"
	}
	return strconv.FormatInt(*value, 10)
}

// ---------------------------------------------------------------------------
// Periods
// ---------------------------------------------------------------------------

// CampsPeriodResult is serializer.data of CampsAndRetreatsPeriodSerializer.
type CampsPeriodResult struct {
	Row      domain.CampsPeriod
	Fee      string
	Services []string
}

func (r *campsRequest) validatePeriod(ctx context.Context, input *dto.CampsPeriodInput, instance *domain.CampsPeriod) (*CampsValidationError, error) {
	errs := input.Errors
	// UniqueValidator excludes the instance; a different free key would make
	// Model.save() insert a copy, which is refused once validation passes.
	keyChange := false
	if input.ID.Present && (instance == nil || input.ID.ID != instance.ID) {
		taken, err := r.tx.PeriodIDTaken(ctx, input.ID.ID)
		if err != nil {
			return nil, err
		}
		if taken {
			errs["camps_and_retreats_period_id"] = []string{"unique:camps and retreats period:camps and retreats period id"}
		} else {
			keyChange = instance != nil
		}
	}
	if input.Camp.Present {
		code, err := r.relation(ctx, input.Camp, r.tx.CampRelated)
		if err != nil {
			return nil, err
		}
		if code != "" {
			errs["camps_and_retreats"] = []string{code}
		}
	}
	for _, item := range input.Services.Items {
		code, err := r.relation(ctx, item, r.tx.ServiceRelated)
		if err != nil {
			return nil, err
		}
		if code != "" {
			errs["camps_and_Retreats_period_services"] = []string{code}
			break
		}
	}
	if failure := campsOrdered(errs, dto.CampsPeriodFieldOrder); failure != nil || !keyChange {
		return failure, nil
	}
	return nil, ErrCampsPrimaryKeyChange
}

func (r *campsRequest) result(ctx context.Context, row domain.CampsPeriod, fee string) (*CampsPeriodResult, error) {
	services, err := r.tx.PeriodServiceIDs(ctx, row.ID)
	if err != nil {
		return nil, err
	}
	return &CampsPeriodResult{Row: row, Fee: fee, Services: services}, nil
}

func campsPeriodChanges(old domain.CampsPeriod, oldFee string, row domain.CampsPeriod, fee string) map[string][2]string {
	changes := map[string][2]string{}
	diff := func(name, before, after string) {
		if before != after {
			changes[name] = [2]string{before, after}
		}
	}
	diff("camps_and_retreats", old.CampID, row.CampID)
	diff("start_date", dto.CampsNaiveUTC(old.Start), dto.CampsNaiveUTC(row.Start))
	diff("end_date", dto.CampsNaiveUTC(old.End), dto.CampsNaiveUTC(row.End))
	diff("title", campsText(old.Title), campsText(row.Title))
	diff("description", campsText(old.Description), campsText(row.Description))
	diff("fee", oldFee, fee)
	diff("max_participants", campsInt(old.Max), campsInt(row.Max))
	diff("created_at", dto.CampsNaiveUTC(old.CreatedAt), dto.CampsNaiveUTC(row.CreatedAt))
	return changes
}

// AddPeriod follows camps_and_retreats_periods_add.
func (s *CampsPeriods) AddPeriod(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool,
	audit domain.LoginAuditContext) (*CampsPeriodResult, error) {
	r, err := s.begin(ctx, identity, audit)
	if err != nil {
		return nil, err
	}
	defer r.tx.Rollback()
	input, invalid, err := dto.ParseCampsPeriodWrite(body, form, false)
	if err != nil {
		return nil, err
	}
	if invalid != nil {
		return nil, campsOrdered(invalid, []string{"non_field_errors"})
	}
	failure, err := r.validatePeriod(ctx, input, nil)
	if err != nil {
		return nil, err
	}
	if failure != nil {
		return nil, failure
	}
	if input.Services.Present {
		// objects.create(**validated_data) with the many-to-many services.
		return nil, &dto.CampsPythonError{Message: "Direct assignment to the forward side of a many-to-many set is prohibited. " +
			"Use camps_and_Retreats_period_services.set() instead."}
	}
	row := domain.CampsPeriod{ID: s.ids(), CampID: input.Camp.ID, Start: input.Start.Value, End: input.End.Value, Fee: "0.00",
		CreatedAt: r.at}
	if input.ID.Present {
		row.ID = input.ID.ID
	}
	// The model defaults: fee 0.00 (a float), max_participants 0.
	fee, max := "0.0", int64(0)
	row.Max = &max
	if input.Title.Present {
		row.Title = input.Title.Value
	}
	if input.Description.Present {
		row.Description = input.Description.Value
	}
	if input.Fee.Present {
		row.Fee, fee = input.Fee.Value, dto.CampsFloatRepr(input.Fee.Value)
	}
	if input.Max.Present {
		row.Max = input.Max.Value
	}
	if input.CreatedAt.Present {
		row.CreatedAt = input.CreatedAt.Value
	}
	camp, err := r.tx.CampRelated(ctx, row.CampID)
	if err != nil {
		return nil, err
	}
	row.CampAssociationID = *camp.AssociationID
	if err = r.tx.InsertPeriod(ctx, row); err != nil {
		return nil, err
	}
	changes := map[string][2]string{"camps_and_retreats_period_id": {"None", row.ID}, "camps_and_retreats": {"None", row.CampID},
		"start_date": {"None", dto.CampsNaiveUTC(row.Start)}, "end_date": {"None", dto.CampsNaiveUTC(row.End)}, "fee": {"None", fee},
		"created_at": {"None", dto.CampsNaiveUTC(row.CreatedAt)}}
	if row.Title != nil {
		changes["title"] = [2]string{"None", *row.Title}
	}
	if row.Description != nil {
		changes["description"] = [2]string{"None", *row.Description}
	}
	if row.Max != nil {
		changes["max_participants"] = [2]string{"None", campsInt(row.Max)}
	}
	association := row.CampAssociationID
	path := "camps_and_retreats.sport_association"
	events := []domain.CampsAudit{{Model: "CampsAndRetreatsPeriod", Key: row.ID, Action: 0, Changes: changes, AssociationID: &association, Path: path}}
	// period.save(): the in-memory float fee against the stored Decimal.
	if stored := row.Fee; stored != fee {
		events = append(events, domain.CampsAudit{Model: "CampsAndRetreatsPeriod", Key: row.ID, Action: 1,
			Changes: map[string][2]string{"fee": {stored, fee}}, AssociationID: &association, Path: path})
	}
	if err = r.emit(ctx, events...); err != nil {
		return nil, err
	}
	result, err := r.result(ctx, row, dto.CampsFloatDecimal(row.Fee))
	if err != nil {
		return nil, err
	}
	return result, r.tx.Commit()
}

// period is the lookup shared by info, update and delete: the period, then
// its camp's association against request.user.sport_association.
func (r *campsRequest) period(ctx context.Context, id string, lock bool, denied string) (*domain.CampsPeriod, error) {
	row, err := r.tx.Period(ctx, id, lock)
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
	if row.CampAssociationID != association {
		return nil, &CampsForbidden{Detail: denied}
	}
	return row, nil
}

// CampsPeriodInfo is CampsAndRetreatsPeriodInfoSerializer's data.
type CampsPeriodInfo struct {
	Period   CampsPeriodResult
	Services []domain.CampsService
}

// PeriodInfo follows camps_and_retreats_periods_info: the role check
// precedes is_valid_uuid.
func (s *CampsPeriods) PeriodInfo(ctx context.Context, identity domain.ResolvedIdentity, raw string) (*CampsPeriodInfo, error) {
	if _, ok := campsRequestUser(identity); !ok {
		return nil, ErrCampsNotAssociation
	}
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return nil, err
	}
	r, err := s.begin(ctx, identity, domain.LoginAuditContext{})
	if err != nil {
		return nil, err
	}
	defer r.tx.Rollback()
	row, err := r.period(ctx, id, false, "You are not allowed to view this")
	if err != nil {
		return nil, err
	}
	result, err := r.result(ctx, *row, row.Fee)
	if err != nil {
		return nil, err
	}
	services, err := r.tx.PeriodServices(ctx, row.ID)
	if err != nil {
		return nil, err
	}
	return &CampsPeriodInfo{Period: *result, Services: services}, nil
}

// UpdatePeriod follows camps_and_retreats_periods_update (partial).
func (s *CampsPeriods) UpdatePeriod(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool,
	audit domain.LoginAuditContext) (*CampsPeriodResult, error) {
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return nil, err
	}
	r, err := s.begin(ctx, identity, audit)
	if err != nil {
		return nil, err
	}
	defer r.tx.Rollback()
	old, err := r.period(ctx, id, true, "You are not allowed editing this.")
	if err != nil {
		return nil, err
	}
	input, invalid, err := dto.ParseCampsPeriodWrite(body, form, true)
	if err != nil {
		return nil, err
	}
	if invalid != nil {
		return nil, campsOrdered(invalid, []string{"non_field_errors"})
	}
	failure, err := r.validatePeriod(ctx, input, old)
	if err != nil {
		return nil, err
	}
	if failure != nil {
		return nil, failure
	}
	row, fee := *old, old.Fee
	if input.Camp.Present {
		row.CampID = input.Camp.ID
		related, err := r.tx.CampRelated(ctx, row.CampID)
		if err != nil {
			return nil, err
		}
		row.CampAssociationID = *related.AssociationID
	}
	if input.Start.Present {
		row.Start = input.Start.Value
	}
	if input.End.Present {
		row.End = input.End.Value
	}
	if input.Title.Present {
		row.Title = input.Title.Value
	}
	if input.Description.Present {
		row.Description = input.Description.Value
	}
	if input.Fee.Present {
		row.Fee, fee = input.Fee.Value, dto.CampsFloatRepr(input.Fee.Value)
	}
	if input.Max.Present {
		row.Max = input.Max.Value
	}
	if input.CreatedAt.Present {
		row.CreatedAt = input.CreatedAt.Value
	}
	if err = r.tx.UpdatePeriod(ctx, row); err != nil {
		return nil, err
	}
	if changes := campsPeriodChanges(*old, old.Fee, row, fee); len(changes) > 0 {
		association := row.CampAssociationID
		if err = r.emit(ctx, domain.CampsAudit{Model: "CampsAndRetreatsPeriod", Key: row.ID, Action: 1, Changes: changes,
			AssociationID: &association, Path: "camps_and_retreats.sport_association"}); err != nil {
			return nil, err
		}
	}
	if input.Services.Present {
		current, err := r.tx.PeriodServiceIDs(ctx, row.ID)
		if err != nil {
			return nil, err
		}
		wanted := map[string]bool{}
		var add []string
		for _, item := range input.Services.Items {
			if !wanted[item.ID] {
				wanted[item.ID] = true
				add = append(add, item.ID)
			}
		}
		existing := map[string]bool{}
		var remove []string
		for _, service := range current {
			existing[service] = true
			if !wanted[service] {
				remove = append(remove, service)
			}
		}
		filtered := add[:0]
		for _, service := range add {
			if !existing[service] {
				filtered = append(filtered, service)
			}
		}
		if err = r.tx.LinkPeriodServices(ctx, row.ID, remove, filtered); err != nil {
			return nil, err
		}
	}
	responseFee := row.Fee
	if input.Fee.Present {
		responseFee = dto.CampsFloatDecimal(row.Fee)
	}
	result, err := r.result(ctx, row, responseFee)
	if err != nil {
		return nil, err
	}
	return result, r.tx.Commit()
}

// DeletePeriod follows camps_and_retreats_periods_delete: the collector also
// deletes the period's services and subscription periods.
func (s *CampsPeriods) DeletePeriod(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	r, err := s.begin(ctx, identity, audit)
	if err != nil {
		return err
	}
	defer r.tx.Rollback()
	if _, err = r.period(ctx, id, true, "You are not allowed deleting this."); err != nil {
		return err
	}
	events, err := r.tx.DeletePeriod(ctx, id)
	if err != nil {
		return err
	}
	for _, event := range events {
		if event, err = r.actorIndexed(ctx, event); err != nil {
			return err
		}
		if err = r.emit(ctx, event); err != nil {
			return err
		}
	}
	return r.tx.Commit()
}

// ---------------------------------------------------------------------------
// Period services
// ---------------------------------------------------------------------------

func (r *campsRequest) validateService(ctx context.Context, input *dto.CampsServiceInput, instance *domain.CampsService) (*CampsValidationError, error) {
	errs := input.Errors
	// UniqueValidator excludes the instance; a different free key would make
	// Model.save() insert a copy, which is refused once validation passes.
	keyChange := false
	if input.ID.Present && (instance == nil || input.ID.ID != instance.ID) {
		taken, err := r.tx.ServiceIDTaken(ctx, input.ID.ID)
		if err != nil {
			return nil, err
		}
		if taken {
			errs["camps_and_retreats_period_service_id"] = []string{"unique:camps and retreats periods service:camps and retreats period service id"}
		} else {
			keyChange = instance != nil
		}
	}
	if input.Period.Present {
		code, err := r.relation(ctx, input.Period, r.tx.PeriodRelated)
		if err != nil {
			return nil, err
		}
		if code != "" {
			errs["camps_and_retreats_period"] = []string{code}
		}
	}
	if input.Category.Present && !input.Category.Null {
		code, err := r.relation(ctx, input.Category, r.tx.CategoryRelated)
		if err != nil {
			return nil, err
		}
		if code != "" {
			errs["payment_category"] = []string{code}
		}
	}
	if failure := campsOrdered(errs, dto.CampsServiceFieldOrder); failure != nil || !keyChange {
		return failure, nil
	}
	return nil, ErrCampsPrimaryKeyChange
}

func applyCampsService(row *domain.CampsService, input *dto.CampsServiceInput) {
	if input.Title.Present {
		row.Title = input.Title.Value
	}
	if input.Description.Present {
		row.Description = input.Description.Value
	}
	if input.Fee.Present {
		row.Fee = input.Fee.Value
	}
	if input.CreatedAt.Present {
		row.CreatedAt = input.CreatedAt.Value
	}
	if input.Period.Present {
		row.PeriodID = input.Period.ID
	}
	if input.Category.Present {
		row.CategoryID = nil
		if !input.Category.Null {
			id := input.Category.ID
			row.CategoryID = &id
		}
	}
}

func campsServiceIndex(ctx context.Context, r *campsRequest, row *domain.CampsService) error {
	related, err := r.tx.PeriodRelated(ctx, row.PeriodID)
	if err != nil {
		return err
	}
	row.CampAssociationID = *related.AssociationID
	return nil
}

// AddService follows camps_and_retreats_periods_services_add.
func (s *CampsPeriods) AddService(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool,
	audit domain.LoginAuditContext) (*domain.CampsService, string, error) {
	r, err := s.begin(ctx, identity, audit)
	if err != nil {
		return nil, "", err
	}
	defer r.tx.Rollback()
	input, invalid, err := dto.ParseCampsServiceWrite(body, form, false)
	if err != nil {
		return nil, "", err
	}
	if invalid != nil {
		return nil, "", campsOrdered(invalid, []string{"non_field_errors"})
	}
	failure, err := r.validateService(ctx, input, nil)
	if err != nil {
		return nil, "", err
	}
	if failure != nil {
		return nil, "", failure
	}
	row := domain.CampsService{ID: s.ids(), Fee: "0.00", CreatedAt: r.at}
	if input.ID.Present {
		row.ID = input.ID.ID
	}
	applyCampsService(&row, input)
	if err = campsServiceIndex(ctx, r, &row); err != nil {
		return nil, "", err
	}
	if err = r.tx.InsertService(ctx, row); err != nil {
		return nil, "", err
	}
	// The model default fee is the float 0.00 until the row is reloaded.
	fee := row.Fee
	if !input.Fee.Present {
		fee = "0.0"
	}
	changes := map[string][2]string{"camps_and_retreats_period_service_id": {"None", row.ID}, "camps_and_retreats_period": {"None", row.PeriodID},
		"fee": {"None", fee}, "created_at": {"None", dto.CampsNaiveUTC(row.CreatedAt)}, "payment_category": {"None", campsText(row.CategoryID)}}
	if row.Title != nil {
		changes["title"] = [2]string{"None", *row.Title}
	}
	if row.Description != nil {
		changes["description"] = [2]string{"None", *row.Description}
	}
	association := row.CampAssociationID
	path := "period.camps_and_retreats.sport_association"
	events := []domain.CampsAudit{{Model: "CampsAndRetreatsPeriodsService", Key: row.ID, Action: 0, Changes: changes, AssociationID: &association,
		Path: path}}
	if fee != row.Fee {
		events = append(events, domain.CampsAudit{Model: "CampsAndRetreatsPeriodsService", Key: row.ID, Action: 1,
			Changes: map[string][2]string{"fee": {row.Fee, fee}}, AssociationID: &association, Path: path})
	}
	if err = r.emit(ctx, events...); err != nil {
		return nil, "", err
	}
	return &row, row.Fee, r.tx.Commit()
}

// service is the lookup shared by update and delete.
func (r *campsRequest) service(ctx context.Context, id string, denied string) (*domain.CampsService, error) {
	row, err := r.tx.Service(ctx, id, true)
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
	if row.CampAssociationID != association {
		return nil, &CampsForbidden{Detail: denied}
	}
	return row, nil
}

// campsPaymentAdapter gives paymentSaveDefaults the two reads it makes.
type campsPaymentAdapter struct {
	domain.PaymentArchiveTransaction
	tx domain.CampsTransaction
}

func (a campsPaymentAdapter) DefaultPaymentCategory(ctx context.Context, association *string) (*string, *string, error) {
	return a.tx.DefaultPaymentCategory(ctx, association)
}

func (a campsPaymentAdapter) PaymentLinkedName(ctx context.Context, payment string) (domain.PaymentLinkedName, error) {
	return a.tx.PaymentLinkedName(ctx, payment)
}

type campsPaymentSave struct {
	row     domain.CampsPayment
	event   *domain.CampsAudit
	failure *dto.CampsPythonError
}

// campsWalk plans the view's payment loop before any write: every save with
// its row and audit entry, then the exception that stops the request.
func (r *campsRequest) campsWalk(ctx context.Context, row domain.CampsService, group *string) ([]campsPaymentSave, error) {
	rows, err := r.tx.WalkRows(ctx, row.PeriodID)
	if err != nil {
		return nil, err
	}
	update := dto.CampsServiceMeta{ServiceID: row.ID, Title: row.Title, Category: campsText(row.CategoryID)}
	update.Amount, _ = strconv.ParseFloat(row.Fee, 64)
	state := map[string]*domain.CampsPayment{}
	var saves []campsPaymentSave
	for _, walkRow := range rows {
		payment := walkRow.Payment
		if current, ok := state[payment.ID]; ok {
			payment = *current
		}
		// auditlog reads the stored payment through its soft-delete,
		// group-aware manager: a payment that manager does not return is
		// saved all the same and each save is audited as an update of every
		// field against None, indexed through the entry's own association.
		hidden := payment.Deleted || (group != nil && (payment.GroupID == nil || *payment.GroupID != *group))
		if hidden && payment.AssociationNull {
			return nil, ErrCampsPaymentNotPorted
		}
		var metas any
		if payment.Metas != nil {
			if metas, err = dto.ParseReferenceJSON([]byte(*payment.Metas)); err != nil {
				return nil, err
			}
		}
		walk := dto.CampsWalkPayment(metas, update, walkRow.PeriodTitle, walkRow.PeriodFee, walkRow.CampTitle, walkRow.CampDescription)
		var association *string
		if !payment.AssociationNull {
			value := payment.AssociationID
			association = &value
		}
		for _, step := range walk.Steps {
			changes := map[string][2]string{}
			if err = paymentSaveDefaults(ctx, campsPaymentAdapter{tx: r.tx}, &payment.PaymentSaveRow, changes); err != nil {
				if errors.Is(err, ErrPaymentSaveFailed) {
					return nil, ErrCampsPaymentNotPorted
				}
				return nil, err
			}
			if step.Final {
				_, stored, ok := dto.CampsStoredAmount(step.Amount)
				if !ok || utf8.RuneCountInString(step.Notes) > 5120 {
					return nil, ErrCampsPaymentNotPorted
				}
				if repr := dto.CampsFloat(step.Amount); repr != payment.Amount {
					changes["amount"] = [2]string{payment.Amount, repr}
				}
				if campsText(payment.Notes) != step.Notes {
					changes["notes"] = [2]string{campsText(payment.Notes), step.Notes}
				}
				notes := step.Notes
				payment.Amount, payment.Notes = stored, &notes
			} else {
				var old any
				if payment.Metas != nil {
					old, _ = dto.ParseReferenceJSON([]byte(*payment.Metas))
				}
				before, after := dto.CampsJSONAudit(old), dto.CampsJSONAudit(step.Meta)
				if before != after {
					changes["meta_payment_categories"] = [2]string{before, after}
				}
				text := dto.CampsJSONText(step.Meta)
				payment.Metas = &text
			}
			save := campsPaymentSave{row: payment}
			if hidden {
				// The instance holds the float amount after the final step; every other field renders as stored.
				full := map[string][2]string{}
				if step.Final {
					full["amount"] = [2]string{"None", dto.CampsFloat(step.Amount)}
				}
				save.event = &domain.CampsAudit{Model: "Payment", Key: payment.ID, Action: 1, Changes: full, AssociationID: association,
					Path: "changes_json.sport_association", Full: true}
			} else if len(changes) > 0 {
				event := domain.CampsAudit{Model: "Payment", Key: payment.ID, Action: 1, Changes: changes, AssociationID: association, Path: "direct"}
				save.event = &event
			}
			saves = append(saves, save)
		}
		copied := payment
		state[payment.ID] = &copied
		if walk.Failure != nil {
			// The loop's order is the database's: a failure with other
			// subscription periods in the walk is not reproduced.
			var python *dto.CampsPythonError
			if len(rows) > 1 || !errors.As(walk.Failure, &python) {
				return nil, ErrCampsPaymentNotPorted
			}
			saves = append(saves, campsPaymentSave{failure: python})
			return saves, nil
		}
	}
	return saves, nil
}

// UpdateService follows camps_and_retreats_periods_services_update.
func (s *CampsPeriods) UpdateService(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool, group string,
	audit domain.LoginAuditContext) (*domain.CampsService, error) {
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return nil, err
	}
	r, err := s.begin(ctx, identity, audit)
	if err != nil {
		return nil, err
	}
	defer r.tx.Rollback()
	old, err := r.service(ctx, id, "You are not allowed editing this.")
	if err != nil {
		return nil, err
	}
	input, invalid, err := dto.ParseCampsServiceWrite(body, form, true)
	if err != nil {
		return nil, err
	}
	if invalid != nil {
		return nil, campsOrdered(invalid, []string{"non_field_errors"})
	}
	failure, err := r.validateService(ctx, input, old)
	if err != nil {
		return nil, err
	}
	if failure != nil {
		return nil, failure
	}
	row := *old
	applyCampsService(&row, input)
	if err = campsServiceIndex(ctx, r, &row); err != nil {
		return nil, err
	}
	// Plan the payment walk on the saved service before writing anything.
	saves, err := r.campsWalk(ctx, row, supplierGroup(group))
	if err != nil {
		return nil, err
	}
	if err = r.tx.UpdateService(ctx, row); err != nil {
		return nil, err
	}
	changes := map[string][2]string{}
	diff := func(name, before, after string) {
		if before != after {
			changes[name] = [2]string{before, after}
		}
	}
	diff("camps_and_retreats_period", old.PeriodID, row.PeriodID)
	diff("title", campsText(old.Title), campsText(row.Title))
	diff("description", campsText(old.Description), campsText(row.Description))
	diff("fee", old.Fee, row.Fee)
	diff("payment_category", campsText(old.CategoryID), campsText(row.CategoryID))
	diff("created_at", dto.CampsNaiveUTC(old.CreatedAt), dto.CampsNaiveUTC(row.CreatedAt))
	if len(changes) > 0 {
		association := row.CampAssociationID
		if err = r.emit(ctx, domain.CampsAudit{Model: "CampsAndRetreatsPeriodsService", Key: row.ID, Action: 1, Changes: changes,
			AssociationID: &association, Path: "period.camps_and_retreats.sport_association"}); err != nil {
			return nil, err
		}
	}
	for _, save := range saves {
		if save.failure != nil {
			if err = r.tx.Commit(); err != nil {
				return nil, err
			}
			return nil, &CampsSaveFailure{Err: save.failure}
		}
		if err = r.tx.SavePayment(ctx, save.row); err != nil {
			return nil, err
		}
		if save.event != nil {
			event := *save.event
			if event.AssociationID == nil {
				if event, err = r.actorIndexed(ctx, event); err != nil {
					return nil, err
				}
			}
			if err = r.emit(ctx, event); err != nil {
				return nil, err
			}
		}
	}
	return &row, r.tx.Commit()
}

// DeleteService follows camps_and_retreats_periods_services_delete.
func (s *CampsPeriods) DeleteService(ctx context.Context, identity domain.ResolvedIdentity, raw string, audit domain.LoginAuditContext) error {
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	r, err := s.begin(ctx, identity, audit)
	if err != nil {
		return err
	}
	defer r.tx.Rollback()
	if _, err = r.service(ctx, id, "You are not allowed deleting this."); err != nil {
		return err
	}
	event, err := r.tx.DeleteService(ctx, id)
	if err != nil {
		return err
	}
	if event, err = r.actorIndexed(ctx, event); err != nil {
		return err
	}
	if err = r.emit(ctx, event); err != nil {
		return err
	}
	return r.tx.Commit()
}
