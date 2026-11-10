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

// CampsAddUUIDError is is_valid_uuid's ValidationError caught by the add view:
// 400 {'error': str(e)}.
type CampsAddUUIDError struct{ Detail string }

func (e *CampsAddUUIDError) Error() string { return e.Detail }

// CampsSingleInvalid is ValidationError({field: 'text'}) (400): the dict
// value stays one ErrorDetail.
type CampsSingleInvalid struct{ Field, Message string }

func (e *CampsSingleInvalid) Error() string { return e.Message }

// CampsFieldInvalid is a standalone UUIDField's run_validation failure (400):
// a list of one ErrorDetail ('null' or 'uuid').
type CampsFieldInvalid struct{ Code string }

func (e *CampsFieldInvalid) Error() string { return e.Code }

// CampsSubscriptions ports the camp subscription list, add, update and delete
// views and the camps_and_retreats_utils helpers they call.
type CampsSubscriptions struct {
	periods *CampsPeriods
	store   domain.CampsSubscriptionsStore
}

func NewCampsSubscriptions(store domain.CampsSubscriptionsStore, now func() time.Time, ids func() string) *CampsSubscriptions {
	return &CampsSubscriptions{periods: &CampsPeriods{now: now, ids: ids}, store: store}
}

type campsSubscriptionRequest struct {
	*campsRequest
	subs  domain.CampsSubscriptionsTransaction
	group string
}

// begin opens the request; owner is request.user (the owner for a
// collaborator, the user itself otherwise).
func (s *CampsSubscriptions) begin(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext,
	group string) (*campsSubscriptionRequest, error) {
	owner, ok := campsRequestUser(identity)
	if !ok {
		owner = identity.ScopeUser.ID
	}
	tx, err := s.store.BeginCampsSubscriptions(ctx)
	if err != nil {
		return nil, err
	}
	return &campsSubscriptionRequest{campsRequest: &campsRequest{s: s.periods, tx: tx, identity: identity, owner: owner,
		audit: paymentCategoryAuditContext(identity, audit), at: s.periods.now().UTC().Truncate(time.Microsecond)}, subs: tx, group: group}, nil
}

func (r *campsSubscriptionRequest) filterGroup() *string { return supplierGroup(r.group) }

// ---------------------------------------------------------------------------
// list
// ---------------------------------------------------------------------------

// CampSubscriptionListItem is CampsAndRetreatsSubscriptionListInfoSerializer's
// data source.
type CampSubscriptionListItem struct {
	Row          domain.CampSubscription
	Registration domain.CampRegistrationBasic
	Current      bool
	Periods      []CampSubscriptionListPeriod
	PaidPeriods  []string
}

type CampSubscriptionListPeriod struct {
	Row      domain.CampSubscriptionPeriod
	Payment  *domain.CourseSubscriptionPayment
	Period   CampsPeriodInfo
	Services []domain.CampsService
}

// List follows camps_and_retreats_subscriptions_list.
func (s *CampsSubscriptions) List(ctx context.Context, identity domain.ResolvedIdentity, raw, group string) ([]CampSubscriptionListItem, error) {
	if _, ok := campsRequestUser(identity); !ok {
		return nil, ErrCampsNotAssociation
	}
	camp, err := messageReadsParseUUID(raw)
	if err != nil {
		return nil, err
	}
	r, err := s.begin(ctx, identity, domain.LoginAuditContext{}, group)
	if err != nil {
		return nil, err
	}
	defer r.tx.Rollback()
	association, err := r.tx.OwnerAssociation(ctx, r.owner)
	if err != nil {
		// request.user.sport_association raises outside any handler.
		return nil, errors.Join(errors.New("camp subscriptions without an association"), err)
	}
	rows, err := r.subs.CampSubscriptions(ctx, association, camp)
	if err != nil {
		return nil, err
	}
	filter := r.filterGroup()
	today := s.periods.now().UTC()
	today = time.Date(today.Year(), today.Month(), today.Day(), 0, 0, 0, 0, time.UTC)
	items := make([]CampSubscriptionListItem, 0, len(rows))
	for _, row := range rows {
		item := CampSubscriptionListItem{Row: row}
		var registration *domain.CampRegistrationBasic
		if row.RegistrationID != nil {
			if registration, err = r.subs.RegistrationBasic(ctx, *row.RegistrationID, filter); err != nil {
				return nil, err
			}
		}
		if registration == nil {
			// Subscription.objects.get() raises DoesNotExist (the 500 page).
			return nil, errors.New("camp subscription registration not found")
		}
		item.Registration = *registration
		item.Current = registration.StartDate == nil || registration.EndDate == nil ||
			(!registration.StartDate.After(today) && !today.After(*registration.EndDate))
		periods, err := r.subs.SubscriptionPeriods(ctx, row.ID)
		if err != nil {
			return nil, err
		}
		for _, period := range periods {
			entry := CampSubscriptionListPeriod{Row: period}
			if period.PaymentID != nil {
				payment, err := r.subs.ListPayment(ctx, *period.PaymentID, filter)
				if err != nil {
					return nil, err
				}
				entry.Payment = &payment
			}
			info, err := r.tx.Period(ctx, period.PeriodID, false)
			if err != nil {
				return nil, err
			}
			ids, err := r.tx.PeriodServiceIDs(ctx, info.ID)
			if err != nil {
				return nil, err
			}
			ordered, err := r.tx.PeriodServices(ctx, info.ID)
			if err != nil {
				return nil, err
			}
			entry.Period = CampsPeriodInfo{Period: CampsPeriodResult{Row: *info, Fee: info.Fee, Services: ids}, Services: ordered}
			if entry.Services, err = r.subs.PeriodServicesUnordered(ctx, info.ID); err != nil {
				return nil, err
			}
			item.Periods = append(item.Periods, entry)
		}
		if item.PaidPeriods, err = r.subs.PaidPeriodsOf(ctx, registration.AssociateID, filter); err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	return items, nil
}

// ---------------------------------------------------------------------------
// add
// ---------------------------------------------------------------------------

// CampSubscriptionResult is CampsAndRetreatsSubscriptionInfoSerializer's data.
type CampSubscriptionResult struct {
	Row     domain.CampSubscription
	Periods []domain.CampSubscriptionPeriod
}

type campSelection struct {
	period   string
	services []string
}

// drfUUID is a standalone serializers.UUIDField().run_validation(value).
func drfUUID(value any) (string, error) {
	if value == nil {
		return "", &CampsFieldInvalid{Code: dto.PaymentCategoryValidationNull}
	}
	id, ok := dto.CampsUUIDValue(value, campsHexUUID)
	if !ok {
		return "", &CampsFieldInvalid{Code: dto.PaymentCategoryValidationUUID}
	}
	return id, nil
}

// campsHexUUID is uuid.UUID(hex=value).
func campsHexUUID(value string) (string, bool) {
	id, err := paymentInfoParseUUID(value)
	if err != nil {
		return "", false
	}
	return id.String(), true
}

// validateSubscription runs the serializer's lookups in field order.
func (r *campsSubscriptionRequest) validateSubscription(ctx context.Context, input *dto.CampSubscriptionInput) (*domain.CampRegistration,
	string, error) {
	errs := input.Errors
	if input.ID.Present {
		taken, err := r.subs.CampSubscriptionIDTaken(ctx, input.ID.ID)
		if err != nil {
			return nil, "", err
		}
		if taken {
			errs["camps_and_retreats_subscription_id"] = []string{"unique:camps and retreats subscription:camps and retreats subscription id"}
		}
	}
	var campAssociation string
	if input.Camp.Present {
		code, err := r.relation(ctx, input.Camp, r.tx.CampRelated)
		if err != nil {
			return nil, "", err
		}
		if code != "" {
			errs["camps_and_retreats"] = []string{code}
		} else {
			related, err := r.tx.CampRelated(ctx, input.Camp.ID)
			if err != nil {
				return nil, "", err
			}
			campAssociation = *related.AssociationID
		}
	}
	var registration *domain.CampRegistration
	if input.Registration.Present && !input.Registration.Null {
		code := input.Registration.Code
		if code == "" {
			found, err := r.subs.RegistrationRelated(ctx, input.Registration.ID, r.filterGroup())
			if err != nil {
				return nil, "", err
			}
			if found == nil {
				code = "related-does-not-exist:" + input.Registration.ID
			} else {
				ok, err := r.belongs(ctx, &found.AssociationID)
				if err != nil {
					return nil, "", err
				}
				if !ok {
					return nil, "", ErrCampsTenant
				}
				registration = found
			}
		}
		if code != "" {
			errs["subscription"] = []string{code}
		}
	}
	if failure := campsOrdered(errs, dto.CampSubscriptionFieldOrder); failure != nil {
		return nil, "", failure
	}
	return registration, campAssociation, nil
}

// enrollmentScope is _validate_camp_enrollment_scope; it returns the
// selected periods with their normalized ids.
func (r *campsSubscriptionRequest) enrollmentScope(ctx context.Context, camp, campAssociation string, registration *domain.CampRegistration,
	periods any) ([]campSelection, error) {
	if registration.AssociationID != campAssociation || registration.Archived || registration.Deleted {
		return nil, &CampsForbidden{Detail: "The registration does not belong to this camp association."}
	}
	user := r.identity.ScopeUser
	switch {
	case user.Role == domain.RoleAssociation || user.Role == domain.RoleCollaborator:
		owner, err := r.subs.CampOwnerUser(ctx, camp)
		if err != nil {
			return nil, err
		}
		if owner != r.owner {
			return nil, &CampsForbidden{Detail: "Camp not available to this association."}
		}
	case user.Role == domain.RoleAthlete:
		direct := registration.UserID == user.ID
		if !direct {
			if registration.AssociateID == nil {
				return nil, &dto.CampsPythonError{Message: "'NoneType' object has no attribute 'user_id'"}
			}
			direct = registration.AssociateUserID != nil && *registration.AssociateUserID == user.ID
		}
		if registration.AssociateID == nil {
			return nil, &dto.CampsPythonError{Message: "'NoneType' object has no attribute 'family_id'"}
		}
		family := false
		if registration.FamilyID != nil {
			found, err := r.subs.FamilyOwned(ctx, user.ID, campAssociation, *registration.FamilyID, r.filterGroup())
			if err != nil {
				return nil, err
			}
			family = found
		}
		if !direct && !family {
			return nil, &CampsForbidden{Detail: "Registration not available to this account."}
		}
	default:
		return nil, &CampsForbidden{Detail: "Account cannot enroll a participant."}
	}
	items, ok := periods.([]any)
	if !ok {
		return nil, &CampsSingleInvalid{Field: "periods", Message: "A list of periods is required."}
	}
	var selections []campSelection
	seen := map[string]bool{}
	for _, item := range items {
		object, ok := item.(domain.RequestObject)
		if !ok {
			return nil, &CampsSingleInvalid{Field: "periods", Message: "Each selected period must be an object."}
		}
		value, _ := object.Lookup("camps_and_retreats_period")
		period, err := drfUUID(value)
		if err != nil {
			return nil, err
		}
		if seen[period] {
			return nil, &CampsSingleInvalid{Field: "periods", Message: "A period can be selected only once."}
		}
		seen[period] = true
		found, err := r.subs.PeriodOfCamp(ctx, period, camp)
		if err != nil {
			return nil, err
		}
		if !found {
			return nil, &CampsForbidden{Detail: "Period does not belong to the selected camp."}
		}
		services, present := object.Lookup("services")
		if !present {
			services = []any{}
		}
		list, ok := services.([]any)
		if !ok {
			return nil, &CampsSingleInvalid{Field: "services", Message: "A list of services is required."}
		}
		selection := campSelection{period: period, services: []string{}}
		known := map[string]bool{}
		for _, entry := range list {
			service, ok := entry.(domain.RequestObject)
			if !ok {
				return nil, &CampsSingleInvalid{Field: "services", Message: "Each selected service must be an object."}
			}
			value, _ := service.Lookup("camps_and_retreats_period_service_id")
			id, err := drfUUID(value)
			if err != nil {
				return nil, err
			}
			if known[id] {
				return nil, &CampsSingleInvalid{Field: "services", Message: "A service can be selected only once."}
			}
			known[id] = true
			found, err := r.subs.ServiceOfPeriod(ctx, id, period)
			if err != nil {
				return nil, err
			}
			if !found {
				return nil, &CampsForbidden{Detail: "Service does not belong to the selected period."}
			}
			selection.services = append(selection.services, id)
		}
		selections = append(selections, selection)
	}
	return selections, nil
}

// createSubscriptionPeriod is create_period_instance with its create entry.
func (r *campsSubscriptionRequest) createSubscriptionPeriod(ctx context.Context, cs domain.CampSubscription, period string) (string, error) {
	id := r.s.ids()
	if err := r.subs.InsertSubscriptionPeriod(ctx, id, period, cs.ID); err != nil {
		return "", err
	}
	association := cs.CampAssociationID
	return id, r.emit(ctx, domain.CampsAudit{Model: "CampsAndRetreatsSubscriptionPeriod", Key: id, Action: 0, Changes: map[string][2]string{
		"camps_and_retreats_subscription_period_id": {"None", id}, "camps_and_retreats_period": {"None", period},
		"camps_and_retreats_subscription": {"None", cs.ID}, "payment": {"None", "None"}},
		AssociationID: &association, Path: "subscription.camps_and_retreats.sport_association"})
}

// generatePayment is generate_payment_for_periods. A *dto.CampsPythonError is
// the exception that stops the view; ErrCampsPaymentNotPorted refuses an
// outcome this port does not reproduce.
func (r *campsSubscriptionRequest) generatePayment(ctx context.Context, cs domain.CampSubscription, campTitle *string,
	registration *domain.CampRegistration) error {
	rows, err := r.subs.GenerateRows(ctx, cs.ID)
	if err != nil || len(rows) == 0 {
		return err
	}
	metas := []any{}
	notes := ""
	amount := 0.0
	for _, row := range rows {
		services, err := r.subs.SelectedServices(ctx, row.ID)
		if err != nil {
			return err
		}
		titles, amounts := []*string{}, []float64{}
		periodAmount := 0.0
		for _, service := range services {
			if service.CategoryID == nil {
				return &dto.CampsPythonError{Message: "'NoneType' object has no attribute 'payment_category_id'"}
			}
			fee, _ := strconv.ParseFloat(service.Fee, 64)
			metas = append(metas, dto.CampsMetaItem(*service.CategoryID, service.ID, service.Title, fee))
			titles, amounts = append(titles, service.Title), append(amounts, fee)
			periodAmount += fee
		}
		fee, _ := strconv.ParseFloat(row.PeriodFee, 64)
		notes += dto.CampsPeriodNotes(row.PeriodTitle, campTitle, row.PeriodDescription, fee, titles, amounts) + "\n\n"
		periodAmount += fee
		amount += periodAmount
	}
	category, categoryName, err := r.tx.DefaultPaymentCategory(ctx)
	if err != nil {
		return err
	}
	owner, err := r.subs.CampOwnerUser(ctx, cs.CampID)
	if err != nil {
		return err
	}
	if registration == nil {
		return &dto.CampsPythonError{Message: "'NoneType' object has no attribute 'associate'"}
	}
	// GroupAwareManager.create takes the X-Group-Id group: only an existing
	// group is ported.
	var group *string
	if r.group != "" {
		if group = supplierGroup(r.group); group == nil {
			return ErrCampsPaymentNotPorted
		}
		found, err := r.subs.GroupExists(ctx, *group)
		if err != nil {
			return err
		}
		if !found {
			return ErrCampsPaymentNotPorted
		}
	}
	_, stored, ok := dto.CampsStoredAmount(amount)
	if !ok || utf8.RuneCountInString(notes) > 5120 {
		return ErrCampsPaymentNotPorted
	}
	account, err := r.subs.DefaultBankAccount(ctx, cs.CampAssociationID)
	if err != nil {
		return err
	}
	payment := domain.CampPaymentCreate{ID: r.s.ids(), Amount: stored, AmountRepr: dto.CampsFloat(amount), CreatedAt: r.at,
		Description: categoryName, CategoryID: category, Metas: dto.CampsJSONText(metas), MetasAudit: dto.CampsJSONAudit(metas), UserID: owner,
		AssociateID: registration.AssociateID, AssociationID: cs.CampAssociationID, AccountID: account, Notes: notes, GroupID: group}
	changes, err := r.subs.CreatePayment(ctx, payment)
	if err != nil {
		return err
	}
	association := cs.CampAssociationID
	if err = r.emit(ctx, domain.CampsAudit{Model: "Payment", Key: payment.ID, Action: 0, Changes: changes, AssociationID: &association,
		Path: "direct"}); err != nil {
		return err
	}
	for _, row := range rows {
		if err = r.subs.SetSubscriptionPeriodPayment(ctx, row.ID, payment.ID); err != nil {
			return err
		}
		if err = r.emit(ctx, domain.CampsAudit{Model: "CampsAndRetreatsSubscriptionPeriod", Key: row.ID, Action: 1,
			Changes: map[string][2]string{"payment": {"None", payment.ID}}, AssociationID: &association,
			Path: "subscription.camps_and_retreats.sport_association"}); err != nil {
			return err
		}
	}
	return nil
}

// Add follows camps_and_retreats_subscriptions_add.
func (s *CampsSubscriptions) Add(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool, group string,
	audit domain.LoginAuditContext) (*CampSubscriptionResult, error) {
	if _, err := messageReadsParseUUID(raw); err != nil {
		var invalid *dto.MessageReadUUIDError
		if errors.As(err, &invalid) {
			return nil, &CampsAddUUIDError{Detail: invalid.Detail}
		}
		return nil, err
	}
	object, err := dto.CampSubscriptionPayload(body, form, raw)
	if err != nil {
		return nil, err
	}
	input := dto.ParseCampSubscriptionWrite(object, form)
	// camps_and_retreats is the path uid, which is_valid_uuid parsed as the
	// field's queryset.get(pk=uid) does.
	camp, _ := campsHexUUID(raw)
	input.Camp = dto.CampsRelation{Present: true, ID: camp}
	r, err := s.begin(ctx, identity, audit, group)
	if err != nil {
		return nil, err
	}
	defer r.tx.Rollback()
	registration, campAssociation, err := r.validateSubscription(ctx, input)
	if err != nil {
		return nil, err
	}
	if !input.Registration.Present {
		// serializer.validated_data['subscription']
		return nil, &dto.CampsPythonError{Message: "'subscription'"}
	}
	if registration == nil {
		return nil, &CampsSingleInvalid{Field: "subscription", Message: "An existing registration is required."}
	}
	periods, present := object.Lookup("periods")
	if !present {
		periods = []any{}
	}
	selections, err := r.enrollmentScope(ctx, input.Camp.ID, campAssociation, registration, periods)
	if err != nil {
		return nil, err
	}
	cs := domain.CampSubscription{ID: s.periods.ids(), CampID: input.Camp.ID, CampAssociationID: campAssociation, Status: 2,
		RegistrationID: &registration.ID, CreatedAt: r.at}
	if input.ID.Present {
		cs.ID = input.ID.ID
	}
	if input.Status.Present {
		cs.Status = input.Status.Value
	}
	if input.CreatedAt.Present {
		cs.CreatedAt = input.CreatedAt.Value
	}
	if err = r.subs.InsertCampSubscription(ctx, cs); err != nil {
		return nil, err
	}
	association := campAssociation
	if err = r.emit(ctx, domain.CampsAudit{Model: "CampsAndRetreatsSubscription", Key: cs.ID, Action: 0, Changes: map[string][2]string{
		"camps_and_retreats_subscription_id": {"None", cs.ID}, "camps_and_retreats": {"None", cs.CampID},
		"status_flag": {"None", strconv.FormatInt(cs.Status, 10)}, "subscription": {"None", registration.ID},
		"created_at": {"None", dto.CampsNaiveUTC(cs.CreatedAt)}}, AssociationID: &association,
		Path: "camps_and_retreats.sport_association"}); err != nil {
		return nil, err
	}
	// The enrollment, its periods and the payment are one transaction.
	for _, selection := range selections {
		id, err := r.createSubscriptionPeriod(ctx, cs, selection.period)
		if err != nil {
			return nil, err
		}
		services, err := r.subs.ExistingServices(ctx, selection.services)
		if err != nil {
			return nil, err
		}
		if err = r.subs.LinkSubscriptionPeriod(ctx, id, services); err != nil {
			return nil, err
		}
	}
	title, err := r.subs.CampTitle(ctx, cs.CampID)
	if err != nil {
		return nil, err
	}
	if err = r.generatePayment(ctx, cs, title, registration); err != nil {
		// Rolled back with everything else.
		return nil, err
	}
	periodsData, err := r.subs.SubscriptionPeriods(ctx, cs.ID)
	if err != nil {
		return nil, err
	}
	return &CampSubscriptionResult{Row: cs, Periods: periodsData}, r.tx.Commit()
}

// ---------------------------------------------------------------------------
// update and delete
// ---------------------------------------------------------------------------

// subscription is the lookup of update and delete: is_valid_uuid, the role
// check, the row (404) and its camp's association.
func (s *CampsSubscriptions) subscription(ctx context.Context, identity domain.ResolvedIdentity, raw, group string,
	audit domain.LoginAuditContext) (*campsSubscriptionRequest, *domain.CampSubscription, error) {
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return nil, nil, err
	}
	if _, ok := campsRequestUser(identity); !ok {
		return nil, nil, ErrCampsNotAssociation
	}
	r, err := s.begin(ctx, identity, audit, group)
	if err != nil {
		return nil, nil, err
	}
	row, err := r.subs.CampSubscription(ctx, id, true)
	if err == nil && row == nil {
		err = ErrCampsNotFound
	}
	if err == nil {
		var association string
		if association, err = r.association(ctx); err == nil && row.CampAssociationID != association {
			err = &CampsForbidden{Detail: "You are not allowed deleting this."}
		}
	}
	if err != nil {
		r.tx.Rollback()
		return nil, nil, err
	}
	return r, row, nil
}

func (r *campsSubscriptionRequest) paymentAudit(ctx context.Context) (domain.CourseSubscriptionAudit, error) {
	actor, err := r.tx.ActorAssociation(ctx, r.identity.Actor.ID)
	return domain.CourseSubscriptionAudit{Context: r.audit, ActorAssociation: actor, At: r.at, Group: r.filterGroup()}, err
}

// deleteUnpaid is delete_unpaid_periods: the instances are loaded first; each
// unpaid payment is deleted once (a later access finds it gone), then the
// subscription period, audited as loaded.
func (r *campsSubscriptionRequest) deleteUnpaid(ctx context.Context, subscription string) error {
	rows, err := r.subs.UnpaidSubscriptionPeriods(ctx, subscription)
	if err != nil {
		return err
	}
	snapshots := make([]map[string][2]string, len(rows))
	for index, row := range rows {
		if snapshots[index], err = r.subs.SubscriptionPeriodChanges(ctx, row.ID); err != nil {
			return err
		}
	}
	audit, err := r.paymentAudit(ctx)
	if err != nil {
		return err
	}
	deleted := map[string]bool{}
	for index, row := range rows {
		if row.PaymentID != nil && !deleted[*row.PaymentID] {
			deleted[*row.PaymentID] = true
			if err = r.subs.DeletePayment(ctx, *row.PaymentID, audit); err != nil {
				return err
			}
		}
		event, err := r.subs.DeleteSubscriptionPeriod(ctx, row.ID, snapshots[index])
		if err != nil {
			return err
		}
		if event, err = r.actorIndexed(ctx, event); err != nil {
			return err
		}
		if err = r.emit(ctx, event); err != nil {
			return err
		}
	}
	return nil
}

// campsModelUUID is Django's UUIDField.to_python for a lookup value: a
// ValidationError quotes str(value).
func campsModelUUID(value any) (string, error) {
	id, ok := dto.CampsUUIDValue(value, campsHexUUID)
	if !ok {
		return "", &dto.CampsPythonError{Message: dto.CampsInvalidUUIDMessage(value)}
	}
	return id, nil
}

// addNewPeriods is add_new_periods over the request's periods as given.
func (r *campsSubscriptionRequest) addNewPeriods(ctx context.Context, cs domain.CampSubscription, periods any) error {
	paid, err := r.subs.PaidPeriodIDs(ctx, cs.ID)
	if err != nil {
		return err
	}
	items, err := dto.CampsPythonIterate(periods)
	if err != nil {
		return err
	}
	for _, period := range items {
		value, err := dto.CampsPythonSubscript(period, "camps_and_retreats_period")
		if err != nil {
			return err
		}
		skip := false
		if text, ok := value.(string); ok {
			for _, existing := range paid {
				skip = skip || existing == text
			}
		}
		if skip {
			continue
		}
		var id string
		if value == nil {
			// get(pk=None) matches nothing.
			return &dto.CampsPythonError{Message: "CampsAndRetreatsPeriod matching query does not exist."}
		}
		if id, err = campsModelUUID(value); err != nil {
			return err
		}
		found, err := r.subs.PeriodExists(ctx, id)
		if err != nil {
			return err
		}
		if !found {
			return &dto.CampsPythonError{Message: "CampsAndRetreatsPeriod matching query does not exist."}
		}
		created, err := r.createSubscriptionPeriod(ctx, cs, id)
		if err != nil {
			return err
		}
		object := period.(domain.RequestObject)
		services, present := object.Lookup("services")
		if !present {
			continue
		}
		entries, err := dto.CampsPythonIterate(services)
		if err != nil {
			return err
		}
		// The list comprehension subscripts every service first; filter()
		// then prepares each value (None is skipped by the lookup).
		values := make([]any, 0, len(entries))
		for _, entry := range entries {
			value, err := dto.CampsPythonSubscript(entry, "camps_and_retreats_period_service_id")
			if err != nil {
				return err
			}
			values = append(values, value)
		}
		var ids []string
		for _, value := range values {
			if value == nil {
				continue
			}
			id, err := campsModelUUID(value)
			if err != nil {
				return err
			}
			ids = append(ids, id)
		}
		existing, err := r.subs.ExistingServices(ctx, ids)
		if err != nil {
			return err
		}
		if err = r.subs.LinkSubscriptionPeriod(ctx, created, existing); err != nil {
			return err
		}
	}
	return nil
}

// campsRequestPeriods is request.data.get('periods', []).
func campsRequestPeriods(body any, form bool) (any, error) {
	switch data := body.(type) {
	case domain.RequestObject:
		value, present := data.Lookup("periods")
		if form {
			for _, field := range data {
				if _, upload := field.Value.(domain.RequestUpload); upload {
					return nil, dto.ErrCampsFormNotPorted
				}
			}
			if present {
				// QueryDict.get of a repeated key (not ported).
				return nil, dto.ErrCampsFormNotPorted
			}
		}
		if !present {
			return []any{}, nil
		}
		return value, nil
	}
	return nil, &dto.CampsPythonError{Message: "'" + dto.CampsTypeName(body) + "' object has no attribute 'get'"}
}

// partial ends a non-atomic view: a Python exception keeps the earlier writes
// (500 {'error': str(e)}); anything else, including a refusal, writes nothing.
func (r *campsSubscriptionRequest) partial(err error) error {
	var python *dto.CampsPythonError
	if errors.As(err, &python) {
		if commit := r.tx.Commit(); commit != nil {
			return commit
		}
		return &CampsSaveFailure{Err: python}
	}
	return err
}

// Update follows camps_and_retreats_subscriptions_update (not atomic).
func (s *CampsSubscriptions) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool, group string,
	audit domain.LoginAuditContext) error {
	r, cs, err := s.subscription(ctx, identity, raw, group, audit)
	if err != nil {
		return err
	}
	defer r.tx.Rollback()
	periods, err := campsRequestPeriods(body, form)
	if err != nil {
		return err
	}
	if err = r.deleteUnpaid(ctx, cs.ID); err != nil {
		return r.partial(err)
	}
	if err = r.addNewPeriods(ctx, *cs, periods); err != nil {
		return r.partial(err)
	}
	title, err := r.subs.CampTitle(ctx, cs.CampID)
	if err != nil {
		return err
	}
	var registration *domain.CampRegistration
	if cs.RegistrationID != nil {
		// subscription.subscription uses the base manager.
		if registration, err = r.subs.RegistrationRelated(ctx, *cs.RegistrationID, nil); err != nil {
			return err
		}
	}
	if err = r.generatePayment(ctx, *cs, title, registration); err != nil {
		return r.partial(err)
	}
	return r.tx.Commit()
}

// Delete follows camps_and_retreats_subscriptions_delete.
func (s *CampsSubscriptions) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw, group string, audit domain.LoginAuditContext) error {
	r, cs, err := s.subscription(ctx, identity, raw, group, audit)
	if err != nil {
		return err
	}
	defer r.tx.Rollback()
	if err = r.deleteUnpaid(ctx, cs.ID); err != nil {
		return r.partial(err)
	}
	events, err := r.subs.DeleteCampSubscription(ctx, cs.ID)
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
