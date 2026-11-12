package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

var (
	// ErrSubscriptionUpdateFailed is an exception the view does not handle (the 500 page).
	ErrSubscriptionUpdateFailed = errors.New("subscription update failed")
	// ErrSubscriptionUpdateNotPorted refuses, before any write, values whose
	// Django conversion the port does not reproduce: date strings other than
	// ASCII YYYY-MM-DD (date.fromisoformat's other formats, non-ASCII digits)
	// and text with NUL characters or lone surrogates.
	ErrSubscriptionUpdateNotPorted = refusal.New("subscription update value not ported")
)

// SubscriptionUpdateInvalid is check_date's ValidationError('Invalid <key> format').
type SubscriptionUpdateInvalid struct{ Key string }

func (e *SubscriptionUpdateInvalid) Error() string { return "Invalid " + e.Key + " format" }

// subscriptionUpdatePublishTimeout bounds the publish after the commit.
const subscriptionUpdatePublishTimeout = 10 * time.Second

type SubscriptionUpdates struct {
	store     domain.SubscriptionUpdateStore
	publisher celery.Publisher
	now       func() time.Time
}

func NewSubscriptionUpdates(store domain.SubscriptionUpdateStore, publisher celery.Publisher, now func() time.Time) *SubscriptionUpdates {
	return &SubscriptionUpdates{store: store, publisher: publisher, now: now}
}

// ParseUUID is is_valid_uuid: a 400 with Python's message.
func (s *SubscriptionUpdates) ParseUUID(raw string) (string, error) {
	return messageReadsParseUUID(raw)
}

// lookup is the view's authorization block: athletes see their own
// subscriptions, everybody else the association's (a missing association is a 500).
func (s *SubscriptionUpdates) lookup(ctx context.Context, tx domain.SubscriptionUpdateTransaction, identity domain.ResolvedIdentity, id,
	groupHeader string) (*domain.SubscriptionUpdateRow, error) {
	lookup := domain.SubscriptionUpdateLookup{Scope: paymentCategoryAssociationScope(identity), Group: supplierGroup(groupHeader)}
	if identity.ScopeUser.Role == domain.RoleAthlete {
		user := identity.ScopeUser.ID
		lookup.User = &user
		row, err := tx.Subscription(ctx, id, lookup)
		if err == nil && row == nil {
			err = domain.ErrSubscriptionUpdateDenied
		}
		return row, err
	}
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID, lookup.Scope)
	if err != nil {
		return nil, err
	}
	row, err := tx.Subscription(ctx, id, lookup)
	switch {
	case err != nil:
		return nil, err
	case row == nil:
		return nil, domain.ErrSubscriptionUpdateNotFound
	case row.AssociationID != association:
		return nil, domain.ErrSubscriptionUpdateDenied
	}
	return row, nil
}

// Target runs the lookups before request.data is parsed.
func (s *SubscriptionUpdates) Target(ctx context.Context, identity domain.ResolvedIdentity, id, groupHeader string) error {
	tx, err := s.store.BeginSubscriptionUpdate(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	_, err = s.lookup(ctx, tx, identity, id, groupHeader)
	return err
}

// subscriptionUpdateViewKeys are the keys the view converts, in order;
// subscriptionUpdateServiceKeys the ones update_subscription assigns.
var (
	subscriptionUpdateViewKeys    = []string{"start_date", "end_date", "type", "role", "subscription_number", "subscription_type"}
	subscriptionUpdateServiceKeys = []string{"type", "role", "start_date", "end_date", "acceptance_date", "resignation_date",
		"subscription_number", "subscription_type", "custom_data", "additional_fields", "status_flag", "notes"}
)

// subscriptionUpdateData is request.data after the view's conversions: the
// assigned keys with their in-memory values (a *big.Int for int() results).
func subscriptionUpdateData(body any, form bool) (map[string]any, error) {
	object, isObject := body.(domain.RequestObject)
	if !isObject {
		// `key in data` (TypeError for None and numbers), then data[key] raises TypeError.
		for _, key := range append(append([]string{}, subscriptionUpdateViewKeys...), subscriptionUpdateServiceKeys...) {
			present, err := dto.PythonContainsKey(body, key)
			if err != nil || present {
				return nil, ErrSubscriptionUpdateFailed
			}
		}
		return map[string]any{}, nil
	}
	data := map[string]any{}
	for _, field := range object {
		data[field.Name] = field.Value // dict / QueryDict: the last value wins
	}
	for _, key := range subscriptionUpdateViewKeys {
		value, present := data[key]
		if !present || value == nil {
			continue
		}
		var converted any
		switch key {
		case "start_date", "end_date":
			text, ok := value.(string)
			if !ok || !checkDate(text) {
				return nil, &SubscriptionUpdateInvalid{Key: key}
			}
			converted = dto.PythonStrip(text) // .strip().capitalize(): digits and dashes only
		case "type", "role":
			if text, isText := value.(string); isText && text == "" {
				converted = big.NewInt(map[string]int64{"type": 2, "role": 1}[key])
				break
			}
			number, result := dto.PythonInt(value)
			if result != dto.PythonIntOK {
				return nil, ErrSubscriptionUpdateFailed
			}
			converted = number
		default:
			converted = dto.PythonStrip(dto.PythonStr(value))
		}
		if form {
			return nil, ErrSubscriptionUpdateFailed // the QueryDict is immutable
		}
		data[key] = converted
	}
	return data, nil
}

// checkDate is check_date's re.match(r'^\d{4}-\d{2}-\d{2}$'): Unicode digits,
// and $ also matches before a final newline.
func checkDate(text string) bool {
	text = strings.TrimSuffix(text, "\n")
	runes := []rune(text)
	if len(runes) != 10 {
		return false
	}
	for index, r := range runes {
		if index == 4 || index == 7 {
			if r != '-' {
				return false
			}
		} else if !unicode.Is(unicode.Nd, r) {
			return false
		}
	}
	return true
}

// subscriptionUpdateValues collects the in-memory instance and whether the
// save would be rejected (the pre_save entry is then kept).
type subscriptionUpdateValues struct {
	changes map[string][2]string
	fails   bool
}

func (v *subscriptionUpdateValues) compare(name, old, new string) {
	if old != new {
		v.changes[name] = [2]string{old, new}
	}
}

// storable: PostgreSQL text and auditlog's jsonb entry refuse NUL characters
// and lone surrogates.
func storable(value any) error {
	if !dto.TextStorable(value) {
		return ErrSubscriptionUpdateNotPorted
	}
	return nil
}

// smallint is PositiveSmallIntegerField: int() at get_prep_value, then the
// smallint range and the >= 0 check constraint.
func (v *subscriptionUpdateValues) smallint(name string, old int, value any) (int, error) {
	if number, converted := value.(*big.Int); converted {
		text := number.String()
		v.compare(name, strconv.Itoa(old), text)
		if !number.IsInt64() || number.Int64() < 0 || number.Int64() > math.MaxInt16 {
			v.fails = true
			return 0, nil
		}
		return int(number.Int64()), nil
	}
	text := dto.PythonStr(value)
	if err := storable(text); err != nil {
		return 0, err
	}
	v.compare(name, strconv.Itoa(old), text)
	if value == nil {
		v.fails = true // NOT NULL
		return 0, nil
	}
	number, result := dto.PythonInt(value)
	if result != dto.PythonIntOK || !number.IsInt64() || number.Int64() < 0 || number.Int64() > math.MaxInt16 {
		v.fails = true
		return 0, nil
	}
	return int(number.Int64()), nil
}

func storedDate(value *time.Time) *string { return lifecycleDateText(lifecycleDate(value)) }

// date is a DateField's in-memory value: auditlog renders smart_str(value);
// to_python parses it at save. stored is YYYY-MM-DD; filled is nil-ness for
// Subscription.save()'s defaults.
func (v *subscriptionUpdateValues) date(value any) (text string, stored *string, err error) {
	switch typed := value.(type) {
	case nil:
		return "None", nil, nil
	case string:
		if err := storable(typed); err != nil {
			return "", nil, err
		}
		if parsed, ok := dto.PythonParseDate(typed); ok {
			return typed, &parsed, nil
		}
		v.fails = true // parse_date finds no date: the field refuses the value at save
		return typed, nil, nil
	}
	text = dto.PythonStr(value)
	if err := storable(text); err != nil {
		return "", nil, err
	}
	v.fails = true // date.fromisoformat(non-str) raises TypeError
	return text, nil, nil
}

func (v *subscriptionUpdateValues) text(name string, old *string, value any, limit int) (*string, error) {
	if value == nil {
		v.compare(name, optionalText(old), "None")
		return nil, nil
	}
	text := dto.PythonStr(value)
	if err := storable(text); err != nil {
		return nil, err
	}
	v.compare(name, optionalText(old), text)
	if limit > 0 && utf8.RuneCountInString(text) > limit {
		v.fails = true
	}
	return &text, nil
}

func jsonFinite(value any) bool {
	switch typed := value.(type) {
	case json.Number:
		if strings.ContainsAny(string(typed), ".eE") {
			number, _ := strconv.ParseFloat(string(typed), 64)
			return !math.IsInf(number, 0)
		}
	case domain.RequestObject:
		for _, field := range typed {
			if !jsonFinite(field.Value) {
				return false
			}
		}
	case []any:
		for _, item := range typed {
			if !jsonFinite(item) {
				return false
			}
		}
	}
	return true
}

// json is a JSONField: auditlog compares json.dumps(sort_keys=True); the
// column stores json.dumps(value) (None is SQL NULL).
func (v *subscriptionUpdateValues) json(name string, old *string, value any) (*string, error) {
	previous, err := dto.CourseWritesStoredJSON(old)
	if err != nil {
		return nil, err
	}
	if !dto.TextStorable(value) {
		v.fails = true // json.dumps escapes NUL and lone surrogates, so the entry is kept; jsonb refuses the column value
	}
	v.compare(name, dto.CourseWritesAuditJSON(previous), dto.CourseWritesAuditJSON(value))
	if !jsonFinite(value) {
		v.fails = true // "Infinity" is not valid jsonb
	}
	return dto.CourseWritesJSONText(value), nil
}

// build is update_subscription's assignments plus Subscription.save().
func subscriptionUpdateBuild(ctx context.Context, row domain.SubscriptionUpdateRow, data map[string]any) (domain.SubscriptionUpdateSave, bool, error) {
	values := &subscriptionUpdateValues{changes: map[string][2]string{}}
	save := domain.SubscriptionUpdateSave{ID: row.ID, AssociationID: row.AssociationID, Type: row.Type, Role: row.Role, Status: row.Status,
		Start: storedDate(row.Start), End: storedDate(row.End), Acceptance: storedDate(row.Acceptance), Resignation: storedDate(row.Resignation),
		Number: row.Number, Kind: row.Kind, Notes: row.Notes, Custom: row.Custom, Additional: row.Additional, Changes: values.changes}
	var err error
	for _, field := range []struct {
		name   string
		old    int
		target *int
	}{{"type", row.Type, &save.Type}, {"role", row.Role, &save.Role}, {"status_flag", row.Status, &save.Status}} {
		if value, present := data[field.name]; present {
			if *field.target, err = values.smallint(field.name, field.old, value); err != nil {
				return save, false, err
			}
		}
	}
	// In-memory dates; nil-ness decides Subscription.save()'s defaults.
	dates := map[string]*string{}
	dateTexts := map[string]string{}
	for _, field := range []struct {
		name   string
		stored *string
		target **string
	}{{"start_date", save.Start, &save.Start}, {"end_date", save.End, &save.End}, {"acceptance_date", save.Acceptance, &save.Acceptance},
		{"resignation_date", save.Resignation, &save.Resignation}} {
		dateTexts[field.name], dates[field.name] = optionalText(field.stored), field.stored
		value, present := data[field.name]
		if !present {
			continue
		}
		text, stored, err := values.date(value)
		if err != nil {
			return save, false, err
		}
		dateTexts[field.name], *field.target = text, stored
		dates[field.name] = stored
		if value != nil && stored == nil {
			dates[field.name] = &text // set (truthy) but unsaveable
		}
	}
	for _, field := range []struct {
		name   string
		limit  int
		target **string
	}{{"subscription_number", 0, &save.Number}, {"subscription_type", 255, &save.Kind}, {"notes", 0, &save.Notes}} {
		if value, present := data[field.name]; present {
			if *field.target, err = values.text(field.name, *field.target, value, field.limit); err != nil {
				return save, false, err
			}
		}
	}
	for _, field := range []struct {
		name   string
		target **string
	}{{"custom_data", &save.Custom}, {"additional_fields", &save.Additional}} {
		if value, present := data[field.name]; present {
			if *field.target, err = values.json(field.name, *field.target, value); err != nil {
				return save, false, err
			}
		}
	}
	// Subscription.save(): missing start/end dates come from the owner's settings.
	rule := domain.SubscriptionDateRule{Duration: row.Duration, Season: domain.CalendarRule{StartDay: row.StartDay, StartMonth: row.StartMonth,
		CustomEnd: row.CustomEnd, EndDay: row.EndDay, EndMonth: row.EndMonth}}
	marker := &domain.CalendarDate{Year: 1, Month: 1, Day: 1}
	existing := domain.SubscriptionDates{}
	if dates["start_date"] != nil {
		existing.Start = marker
	}
	if dates["end_date"] != nil {
		existing.End = marker
	}
	filled, err := ResolveSubscriptionDates(ctx, row.Created.UTC(), rule, existing)
	if err != nil {
		return save, false, err
	}
	if existing.Start == nil {
		save.Start = lifecycleDateText(filled.Start)
		dateTexts["start_date"] = optionalText(save.Start)
	}
	if existing.End == nil {
		save.End = lifecycleDateText(filled.End)
		dateTexts["end_date"] = optionalText(save.End)
	}
	for _, field := range []struct {
		name   string
		stored *time.Time
	}{{"start_date", row.Start}, {"end_date", row.End}, {"acceptance_date", row.Acceptance}, {"resignation_date", row.Resignation}} {
		values.compare(field.name, optionalText(storedDate(field.stored)), dateTexts[field.name])
	}
	// document_pdf = None
	if row.DocumentID != nil {
		values.changes["document_pdf"] = [2]string{*row.DocumentID, "None"}
	}
	return save, values.fails, nil
}

// Update follows subscription_update after the lookups: request.data's
// conversions, update_subscription and Subscription.save() (committed), then
// print_document_subscription.delay(str(subscription_id), authorization).
func (s *SubscriptionUpdates) Update(ctx context.Context, identity domain.ResolvedIdentity, id, groupHeader string, body any, form bool,
	authorization *string, login domain.LoginAuditContext) error {
	tx, err := s.store.BeginSubscriptionUpdate(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	row, err := s.lookup(ctx, tx, identity, id, groupHeader)
	if err != nil {
		return err
	}
	data, err := subscriptionUpdateData(body, form)
	if err != nil {
		return err
	}
	save, fails, err := subscriptionUpdateBuild(ctx, *row, data)
	if err != nil {
		return err
	}
	actor, err := tx.ActorAssociation(ctx, identity.Actor.ID)
	if err != nil {
		return err
	}
	audit := domain.CourseSubscriptionAudit{Context: paymentCategoryAuditContext(identity, login), ActorAssociation: actor,
		At: s.now().UTC().Truncate(time.Microsecond), Group: supplierGroup(groupHeader)}
	if fails {
		// auditlog's pre_save entry is committed before the rejected UPDATE.
		if err = tx.AuditOnly(ctx, save, audit); err != nil {
			return err
		}
		if err = tx.Commit(); err != nil {
			return err
		}
		return ErrSubscriptionUpdateFailed
	}
	if err = tx.SaveSubscription(ctx, save, audit); err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	publish, cancel := context.WithTimeout(context.WithoutCancel(ctx), subscriptionUpdatePublishTimeout)
	defer cancel()
	var token any
	if authorization != nil {
		token = *authorization
	}
	_, err = s.publisher.Publish(publish, celery.Task{Name: "print_document_subscription", Args: []any{row.ID, token}})
	if err != nil {
		return fmt.Errorf("print_document_subscription: %w", err)
	}
	return nil
}
