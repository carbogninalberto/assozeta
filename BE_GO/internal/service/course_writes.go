package service

import (
	"context"
	"crypto/rand"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// CourseWrites ports course_add, course_update, course_delete,
// course_overview_update and course_overview_delete. Django runs them
// without ATOMIC_REQUESTS: work saved before a late error stays, so those
// paths commit before answering (late).
type CourseWrites struct {
	store domain.CourseWritesStore
	now   func() time.Time
	newID func() string
}

func NewCourseWrites(store domain.CourseWritesStore, now func() time.Time, newID func() string) *CourseWrites {
	return &CourseWrites{store: store, now: now, newID: newID}
}

// CourseWritesEventInvalid is a CourseEventsSerializer error raised as is (400).
type CourseWritesEventInvalid struct{ Fields map[string][]string }

func (e *CourseWritesEventInvalid) Error() string { return "invalid course event" }

// CourseWritesRequest carries the request inputs the views read.
type CourseWritesRequest struct {
	Body        any
	GroupHeader string
	Audit       domain.LoginAuditContext
}

// late commits the work Django would already have saved, then answers err.
func courseWritesLate(tx domain.CourseWritesTransaction, err error) error {
	if commit := tx.Commit(); commit != nil {
		return commit
	}
	return err
}

func (s *CourseWrites) begin(ctx context.Context, identity domain.ResolvedIdentity) (domain.CourseWritesTransaction, error) {
	// scoped_queryset() during an association-scoped impersonation is not ported.
	if identity.ImpersonationAssociationID != "" {
		return nil, domain.ErrCourseWritesNotPorted
	}
	return s.store.BeginCourseWrites(ctx)
}

func (s *CourseWrites) auditContext(ctx context.Context, tx domain.CourseWritesTransaction, identity domain.ResolvedIdentity,
	request CourseWritesRequest) (domain.CourseSubscriptionAudit, error) {
	actor, err := tx.ActorAssociation(ctx, identity.Actor.ID)
	if err != nil {
		return domain.CourseSubscriptionAudit{}, err
	}
	return domain.CourseSubscriptionAudit{Context: paymentCategoryAuditContext(identity, request.Audit), ActorAssociation: actor,
		At: s.now().UTC().Truncate(time.Microsecond), Group: supplierGroup(request.GroupHeader)}, nil
}

// createGroup is GroupAwareManager.create's raw X-Group-Id: a value the
// UUIDField rejects, or an unknown group (deferred foreign key), fails the
// create; a non-canonical spelling would be audited as sent (not ported).
func (s *CourseWrites) createGroup(ctx context.Context, tx domain.CourseWritesTransaction, header string) (*string, error) {
	if header == "" {
		return nil, nil
	}
	group := supplierGroup(header)
	if group == nil {
		return nil, domain.ErrCourseWritesServer
	}
	if *group != header {
		return nil, domain.ErrCourseWritesNotPorted
	}
	exists, err := tx.GroupExists(ctx, *group)
	if err != nil {
		return nil, err
	}
	if !exists {
		return nil, domain.ErrCourseWritesServer
	}
	return group, nil
}

// --- course/<uid>/delete ------------------------------------------------------------

// Delete follows course_delete: is_valid_uuid, the owner's association (a
// miss is a 500), the live group-aware course (404), its
// association check, then course.delete().
func (s *CourseWrites) Delete(ctx context.Context, identity domain.ResolvedIdentity, raw string, request CourseWritesRequest) error {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return err
	}
	tx, err := s.begin(ctx, identity)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return err
	}
	course, err := tx.LiveCourse(ctx, id.String(), supplierGroup(request.GroupHeader))
	if err != nil {
		return err
	}
	if course == nil {
		return domain.ErrCourseWritesNotFound
	}
	if course.AssociationID == nil {
		return domain.ErrCourseWritesServer
	}
	if *course.AssociationID != association {
		return domain.ErrCourseWritesForeign
	}
	audit, err := s.auditContext(ctx, tx, identity, request)
	if err != nil {
		return err
	}
	if err = tx.DeleteCourse(ctx, *course, audit); err != nil {
		return err
	}
	return tx.Commit()
}

// --- course/<uid>/update ------------------------------------------------------------

func courseWritesText(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

func courseWritesStoredJSON(text *string) (string, error) {
	value, err := dto.CourseWritesStoredJSON(text)
	if err != nil {
		return "", err
	}
	if text == nil {
		return "null", nil
	}
	return dto.CourseWritesAuditJSON(value), nil
}

func courseWritesMoment(value *time.Time) string {
	if value == nil {
		return "None"
	}
	return dto.CourseWritesNaiveUTC(*value)
}

func courseWritesFrequency(value *int) string {
	if value == nil {
		return "None"
	}
	return strconv.Itoa(*value)
}

// courseAuditValues are auditlog's str() renderings of the fields
// course_update can assign.
func courseAuditValues(row domain.CourseWriteRow) (map[string]string, error) {
	quotes, err := courseWritesStoredJSON(row.MultipleQuotes)
	if err != nil {
		return nil, err
	}
	events, err := courseWritesStoredJSON(row.Events)
	if err != nil {
		return nil, err
	}
	return map[string]string{
		"title": courseWritesText(row.Title), "description": courseWritesText(row.Description), "one_fee": row.OneFee,
		"fee": courseWritesText(row.Fee), "multiple_quotes": quotes, "events": events,
		"billed_duration_is_sport_season": pythonBool(row.SportSeason), "billed_frequency": courseWritesFrequency(row.BilledFrequency),
		"billed_from_subscription_date": pythonBool(row.FromSubscription), "billed_from_day_of_month": strconv.Itoa(row.BilledDayOfMonth),
		"auto_renewal": pythonBool(row.AutoRenewal), "start_date": courseWritesMoment(row.StartDate), "end_date": courseWritesMoment(row.EndDate),
	}, nil
}

// courseWritesLocations replaces nothing itself: it adds each listed
// location of the association, or already linked to the course
// (CourseLocation.objects.filter(...).first()).
// failed reports Django's exception (the caller commits and answers 500).
func courseWritesLocations(ctx context.Context, tx domain.CourseWritesTransaction, course, association string, linked []string,
	value any) (failed bool, err error) {
	items, ok := dto.PythonIterate(value)
	if !ok {
		return true, nil
	}
	for _, item := range items {
		object, ok := item.(domain.RequestObject)
		if !ok {
			return true, nil
		}
		raw, present := object.Lookup("course_location_id")
		if !present {
			return true, nil
		}
		if raw == nil {
			continue
		}
		id, ok := dto.DjangoUUIDLookup(raw)
		if !ok {
			return true, nil
		}
		exists, err := tx.LocationLinkable(ctx, id, association, linked)
		if err != nil {
			return false, err
		}
		if exists {
			if err = tx.AddLocation(ctx, course, id); err != nil {
				return false, err
			}
		}
	}
	return false, nil
}

type courseUpdateDate struct {
	present, valid bool
	moment         *time.Time
}

// Update follows course_update: is_valid_uuid, the live group-aware course
// (400 {'msg:': 'course not found.'}), the caller's association (403 unless
// it is the course's), then each body value in the view's order,
// Course.save() and the title signal.
func (s *CourseWrites) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, request CourseWritesRequest) error {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return err
	}
	tx, err := s.begin(ctx, identity)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	group := supplierGroup(request.GroupHeader)
	course, err := tx.LiveCourse(ctx, id.String(), group)
	if err != nil {
		return err
	}
	if course == nil {
		return domain.ErrCourseWritesUpdateNotFound
	}
	association, err := tx.OwnerAssociationFirst(ctx, identity.ScopeUser.ID)
	if err != nil {
		return err
	}
	if association == nil || course.AssociationID == nil || *course.AssociationID != *association {
		return domain.ErrCourseWritesForeign
	}
	data := domain.RequestObject{}
	if request.Body != nil {
		object, ok := request.Body.(domain.RequestObject)
		if !ok {
			// data.keys() of a list or scalar body.
			return domain.ErrCourseWritesServer
		}
		data = object
	}
	before, err := courseAuditValues(*course)
	if err != nil {
		return err
	}
	row := *course
	after := map[string]string{}
	for k, v := range before {
		after[k] = v
	}
	// Values assigned before any write; inputs whose save Django handles
	// differently are refused here.
	for _, name := range []string{"title", "description"} {
		value, present := data.Lookup(name)
		if !present {
			continue
		}
		text, ok := value.(string)
		if !ok {
			return domain.ErrCourseWritesServer
		}
		escaped := dto.CourseWritesEscape(text)
		if name == "title" {
			row.Title = &escaped
		} else {
			row.Description = &escaped
		}
		after[name] = escaped
	}
	if value, present := data.Lookup("one_fee"); present && course.OneFeePayment {
		stored, text, ok := dto.CourseUpdateDecimal(value)
		if !ok {
			return domain.ErrCourseWritesNotPorted
		}
		row.OneFee, after["one_fee"] = stored, text
	}
	if value, present := data.Lookup("fee"); present {
		if value == nil {
			row.Fee, after["fee"] = nil, "None"
		} else {
			stored, text, ok := dto.CourseUpdateDecimal(value)
			if !ok {
				return domain.ErrCourseWritesNotPorted
			}
			row.Fee, after["fee"] = &stored, text
		}
	}
	locations, locationsPresent := data.Lookup("locations")
	if value, present := data.Lookup("multiple_quotes"); present {
		row.MultipleQuotes, after["multiple_quotes"] = dto.CourseWritesJSONText(value), dto.CourseWritesAuditJSON(value)
	}
	events, eventsPresent := data.Lookup("events")
	eventsPresent = eventsPresent && course.MultiPayments
	if eventsPresent {
		if items, ok := dto.PythonIterate(events); ok {
			for _, item := range items {
				if _, _, err := dto.ValidateCourseEvent(item); err != nil {
					return err
				}
			}
		}
	}
	for _, field := range []struct {
		name   string
		target *bool
	}{{"billed_duration_is_sport_season", &row.SportSeason}, {"billed_from_subscription_date", &row.FromSubscription},
		{"auto_renewal", &row.AutoRenewal}} {
		if value, present := data.Lookup(field.name); present {
			flag, text, ok := dto.CourseUpdateBoolean(value)
			if !ok {
				return domain.ErrCourseWritesNotPorted
			}
			*field.target, after[field.name] = flag, text
		}
	}
	if value, present := data.Lookup("billed_frequency"); present {
		if value == nil {
			row.BilledFrequency, after["billed_frequency"] = nil, "None"
		} else {
			number, ok := dto.CourseUpdateInteger(value, 0, 32767)
			if !ok {
				return domain.ErrCourseWritesNotPorted
			}
			row.BilledFrequency, after["billed_frequency"] = &number, strconv.Itoa(number)
		}
	}
	if value, present := data.Lookup("billed_from_day_of_month"); present {
		number, ok := dto.CourseUpdateInteger(value, -2147483648, 2147483647)
		if !ok {
			return domain.ErrCourseWritesNotPorted
		}
		row.BilledDayOfMonth, after["billed_from_day_of_month"] = number, strconv.Itoa(number)
	}
	dates := map[string]*courseUpdateDate{}
	for _, name := range []string{"start_date", "end_date"} {
		value, present := data.Lookup(name)
		if !present {
			continue
		}
		date := &courseUpdateDate{present: true, valid: true}
		if value != nil {
			text, ok := value.(string)
			if !ok {
				return domain.ErrCourseWritesNotPorted
			}
			moment, valid, ok := dto.CourseUpdateDateTime(text)
			if !ok {
				return domain.ErrCourseWritesNotPorted
			}
			date.valid = valid
			if valid {
				date.moment = &moment
			}
		}
		dates[name] = date
	}
	audit, err := s.auditContext(ctx, tx, identity, request)
	if err != nil {
		return err
	}
	// The writes, in the view's order: locations are replaced immediately.
	if locationsPresent {
		linked, err := tx.LinkedLocations(ctx, course.ID)
		if err != nil {
			return err
		}
		if err = tx.ClearLocations(ctx, course.ID); err != nil {
			return err
		}
		failed, err := courseWritesLocations(ctx, tx, course.ID, *association, linked, locations)
		if err != nil {
			return err
		}
		if failed {
			return courseWritesLate(tx, domain.ErrCourseWritesServer)
		}
	}
	if eventsPresent {
		items, ok := dto.PythonIterate(events)
		if !ok {
			return courseWritesLate(tx, domain.ErrCourseWritesServer)
		}
		stored := []any{}
		for _, item := range items {
			event, invalid, err := dto.ValidateCourseEvent(item)
			if err != nil {
				return err
			}
			if invalid != nil {
				return courseWritesLate(tx, &CourseWritesEventInvalid{Fields: invalid})
			}
			stored = append(stored, domain.RequestObject{{Name: "id", Value: json.Number(strconv.Itoa(event.ID))},
				{Name: "amount", Value: event.Amount}, {Name: "payment_date", Value: event.Date.Format("02/01/2006")}})
		}
		row.Events, after["events"] = dto.CourseWritesJSONText(stored), dto.CourseWritesAuditJSON(stored)
	}
	for name, date := range dates {
		if !date.valid {
			// DateTimeField.to_python raises in auditlog's pre_save.
			return courseWritesLate(tx, domain.ErrCourseWritesServer)
		}
		after[name] = courseWritesMoment(date.moment)
		if name == "start_date" {
			row.StartDate = date.moment
		} else {
			row.EndDate = date.moment
		}
	}
	changes := map[string][2]string{}
	for name, value := range after {
		if before[name] != value {
			changes[name] = [2]string{before[name], value}
		}
	}
	if err = tx.SaveCourse(ctx, domain.CourseWriteCourseSave{Row: row, Changes: changes}, audit); err != nil {
		return err
	}
	// Course.save() dispatches update_metadata_courses when the title changed.
	if !sameText(course.Title, row.Title) {
		if err = s.rewriteTitle(ctx, tx, course.Title, *row.Title, *association, group); err != nil {
			if errors.Is(err, domain.ErrCourseWritesServer) {
				return courseWritesLate(tx, err)
			}
			return err
		}
	}
	return tx.Commit()
}

// rewriteTitle is update_metadata_courses_callback: the course association's
// live group-aware payments whose meta text or description contains the old title
// (case-insensitively) get it replaced (case-sensitively) in their top-level
// meta strings and description, written with bulk_update (no audit).
func (s *CourseWrites) rewriteTitle(ctx context.Context, tx domain.CourseWritesTransaction, old *string, title, association string,
	group *string) error {
	if old == nil || *old == "" {
		return nil
	}
	payments, err := tx.TitlePayments(ctx, *old, association, group)
	if err != nil {
		return err
	}
	type rewrite struct {
		id                string
		meta, description *string
	}
	rewrites := []rewrite{}
	for _, payment := range payments {
		changed := false
		meta, description := payment.Meta, payment.Description
		if payment.Meta != nil {
			value, err := dto.ParseReferenceJSON([]byte(*payment.Meta))
			if err != nil {
				return err
			}
			if dto.PythonTruthy(value) {
				object, ok := value.(domain.RequestObject)
				if !ok {
					// .items() of a meta that is not a dict.
					return domain.ErrCourseWritesServer
				}
				replaced := make(domain.RequestObject, len(object))
				metaChanged := false
				for index, field := range object {
					replaced[index] = field
					if text, ok := field.Value.(string); ok {
						if next := strings.ReplaceAll(text, *old, title); next != text {
							replaced[index].Value, metaChanged = next, true
						}
					}
				}
				if metaChanged {
					meta, changed = dto.CourseWritesJSONText(replaced), true
				}
			}
		}
		if payment.Description != nil && *payment.Description != "" && strings.Contains(*payment.Description, *old) {
			next := strings.ReplaceAll(*payment.Description, *old, title)
			description, changed = &next, true
		}
		if changed {
			rewrites = append(rewrites, rewrite{id: payment.ID, meta: meta, description: description})
		}
	}
	for _, item := range rewrites {
		if err = tx.RewritePayment(ctx, item.id, item.meta, item.description); err != nil {
			return err
		}
	}
	return nil
}

// --- course/add ------------------------------------------------------------------------

func courseWritesColor() string {
	var raw [4]byte
	_, _ = rand.Read(raw[:])
	return fmt.Sprintf("#%06x", binary.BigEndian.Uint32(raw[:])&0xFFFFFF)
}

// Add follows course_add.
func (s *CourseWrites) Add(ctx context.Context, identity domain.ResolvedIdentity, request CourseWritesRequest) error {
	data := domain.RequestObject{}
	if request.Body != nil {
		object, ok := request.Body.(domain.RequestObject)
		if !ok {
			// data.keys() of a list or scalar body.
			return domain.ErrCourseWritesServer
		}
		data = object
	}
	newCourse, hasCourse := data.Lookup("new_course")
	subscriptions, hasSubscriptions := data.Lookup("subscriptions")
	if !hasCourse || !hasSubscriptions || len(data) != 2 {
		return domain.ErrCourseWritesBodyKeys
	}
	tx, err := s.begin(ctx, identity)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return err
	}
	input, err := dto.ValidateCourseAdd(newCourse)
	if err != nil {
		return err
	}
	if input.Title == nil || input.Description == nil {
		// validated_data['title'].replace(...): KeyError or AttributeError.
		return domain.ErrCourseWritesServer
	}
	group, err := s.createGroup(ctx, tx, request.GroupHeader)
	if err != nil {
		return err
	}
	audit, err := s.auditContext(ctx, tx, identity, request)
	if err != nil {
		return err
	}
	title, description := dto.CourseWritesEscape(*input.Title), dto.CourseWritesEscape(*input.Description)
	color := courseWritesColor()
	fee := input.Fee
	row := domain.CourseWriteRow{ID: s.newID(), StatusFlag: 2, CourseType: input.CourseType, CreationDate: s.now().UTC().Truncate(time.Microsecond),
		Title: &title, Description: &description, MultiPayments: input.Multi, Fee: &fee, Events: dto.CourseWritesJSONText(input.Events),
		OneFeePayment: input.OneFeePayment, OneFee: input.OneFee, Color: &color, MultipleQuotes: dto.CourseWritesJSONText(input.MultipleQuotes),
		SportSeason: input.SportSeason, BilledFrequency: input.BilledFrequency, FromSubscription: input.FromSubscription,
		BilledDayOfMonth: input.BilledDayOfMonth, AutoRenewal: input.AutoRenewal, GroupID: group}
	overrides := map[string][2]string{}
	changes := map[string][2]string{"sport_association": {"None", association}}
	for name, defaulted := range map[string]bool{"fee": input.FeeDefault, "one_fee": input.OneFeeDefault} {
		if defaulted {
			// The serializer's float default: str(0.0).
			overrides[name] = [2]string{"None", "0.0"}
			changes[name] = [2]string{"0.00", "0.0"}
		}
	}
	if err = tx.CreateCourse(ctx, row, overrides, audit); err != nil {
		return err
	}
	if err = tx.SetCourseAssociation(ctx, row.ID, association, changes, audit); err != nil {
		return err
	}
	courseObject := newCourse.(domain.RequestObject)
	if locations, present := courseObject.Lookup("locations"); present {
		failed, err := courseWritesLocations(ctx, tx, row.ID, association, nil, locations)
		if err != nil {
			return err
		}
		if failed {
			return courseWritesLate(tx, domain.ErrCourseWritesServer)
		}
	}
	items, ok := dto.PythonIterate(subscriptions)
	if !ok {
		return courseWritesLate(tx, domain.ErrCourseWritesServer)
	}
	category, err := tx.DefaultPaymentCategory(ctx, &association)
	if err != nil {
		return err
	}
	bank, err := tx.DefaultBankAccount(ctx, &association)
	if err != nil {
		return err
	}
	plan := false
	if input.Multi && len(items) > 0 {
		if plan, err = tx.UserFullInstallmentsPlan(ctx, identity.ScopeUser.ID); err != nil {
			return err
		}
	}
	for _, item := range items {
		if item == nil {
			return courseWritesLate(tx, domain.ErrCourseWritesServer)
		}
		id, ok := dto.DjangoUUIDLookup(item)
		if !ok {
			return courseWritesLate(tx, domain.ErrCourseWritesServer)
		}
		subscription, err := tx.LiveSubscription(ctx, id, audit.Group)
		if err != nil {
			return err
		}
		if subscription == nil || subscription.AssociationID == nil || *subscription.AssociationID != association {
			// Subscription.objects.get(subscription_id=..., sport_association=...): DoesNotExist.
			return courseWritesLate(tx, domain.ErrCourseWritesServer)
		}
		zero := "0.00"
		cs := domain.CourseSubscriptionRow{ID: s.newID(), CourseID: row.ID, CreationDate: s.now().UTC().Truncate(time.Microsecond),
			SubscriptionID: &subscription.ID, MultiPayments: input.Multi, Type: 1, BilledFrequency: 1, BilledFromDayOfMonth: 1,
			MembershipActive: true, MembershipFee: &zero, CourseAssociationID: &association}
		if err = tx.CreateCourseSubscription(ctx, cs, audit); err != nil {
			return err
		}
		account := bank
		if subscription.UserID == nil {
			account = nil
		}
		payment := func(amount, memory string, created time.Time) domain.CourseWritePayment {
			return domain.CourseWritePayment{ID: s.newID(), Amount: amount, MemoryAmount: memory, CreationDate: created, Description: "Quota Corso",
				CategoryID: category, UserID: subscription.UserID, AssociateID: subscription.AssociateID, AssociationID: &association,
				AccountID: account, GroupID: group}
		}
		if input.Multi {
			created := time.Date(cs.CreationDate.Year(), cs.CreationDate.Month(), cs.CreationDate.Day(), 0, 0, 0, 0, time.UTC)
			for _, event := range input.EventItems {
				if event.Date.Before(created) && !plan {
					continue
				}
				write := payment(event.Amount, event.Amount, event.Date)
				if err = tx.CreatePayment(ctx, write, audit); err != nil {
					return err
				}
				if err = tx.CreateInstallment(ctx, domain.CourseWriteInstallmentCreate{ID: s.newID(), CourseSubscription: cs.ID, Number: event.ID,
					Amount: event.Amount, PaymentDate: event.Date, PaymentID: write.ID, GroupID: group, AssociationID: &association}, audit); err != nil {
					return err
				}
			}
		} else {
			memory := input.Fee
			if input.FeeDefault {
				memory = "0.0"
			}
			write := payment(input.Fee, memory, s.now().UTC().Truncate(time.Microsecond))
			if err = tx.CreatePayment(ctx, write, audit); err != nil {
				return err
			}
			cs.PaymentID = &write.ID
		}
		if err = tx.SaveCourseSubscription(ctx, domain.CourseWriteSubscriptionSave{Row: cs, FloatFee: true}, audit); err != nil {
			return err
		}
	}
	return tx.Commit()
}
