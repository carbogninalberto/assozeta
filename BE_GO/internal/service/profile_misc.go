package service

import (
	"context"
	"encoding/json"
	"errors"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ProfileMisc implements profile_associates_course,
// profile_associates_sport_association, profile_image and onboarding_update.
type ProfileMisc struct {
	reader domain.ProfileAssociatesReader
	store  domain.OnboardingStore
	now    func() time.Time
}

func NewProfileMisc(reader domain.ProfileAssociatesReader, store domain.OnboardingStore, now func() time.Time) *ProfileMisc {
	return &ProfileMisc{reader: reader, store: store, now: now}
}

// ErrProfileMiscServer is an exception Django renders as the 500 page.
var ErrProfileMiscServer = errors.New("profile misc server error")

// ErrOnboardingNotPorted refuses, before any committed write, inputs whose
// Django behaviour depends on database errors or model paths this port does
// not reproduce (see profile-misc.md).
var ErrOnboardingNotPorted = refusal.New("onboarding update input not ported")

// OnboardingServerError is the view's `except Exception`: 500 {'error': str(e)}.
type OnboardingServerError struct{ Message string }

func (e *OnboardingServerError) Error() string { return e.Message }

// OnboardingValidationError is the view's `except ValidationError`: 400 {'error': str(e)}.
type OnboardingValidationError struct{ Fields []dto.DRFFieldError }

func (e *OnboardingValidationError) Error() string { return "onboarding validation error" }

// OnboardingFreshError is the fresh self-host payload refusal.
type OnboardingFreshError struct{ Details map[string]string }

func (e *OnboardingFreshError) Error() string { return "fresh onboarding payload incomplete" }

var ErrOnboardingNotAssociation = errors.New("You are not a sport association.")

// requestUser is request.user: collaborators act as their connected owner.
func profileMiscRequestUser(identity domain.ResolvedIdentity) (string, int, error) {
	if identity.ScopeUser == nil || identity.ScopeUser.ID == "" {
		return "", 0, domain.ErrInvalidScopedRequest
	}
	user := identity.ScopeUser
	if user.Role == domain.RoleCollaborator {
		if user.ConnectedUserID == nil {
			return "", 0, domain.ErrAssociationNotFound
		}
		return *user.ConnectedUserID, domain.RoleAssociation, nil
	}
	return user.ID, user.Role, nil
}

func profileMiscScope(identity domain.ResolvedIdentity, group string) domain.ProfileAssociateScope {
	scope := domain.ProfileAssociateScope{Group: supplierGroup(group)}
	if identity.ImpersonationAssociationID != "" {
		association := identity.ImpersonationAssociationID
		scope.Association = &association
		person := identity.ScopeUser
		if identity.Effective != nil {
			person = identity.Effective
		}
		if person != nil {
			scope.Person, scope.Family = person.ID, person.Role == domain.RoleAthlete
		}
	}
	return scope
}

func profileAssociateEntry(row domain.ProfileAssociate) map[string]any {
	name := func(value *string) string {
		if value == nil {
			return "None"
		}
		return *value
	}
	// '%s %s' % (first_name, last_name), stripped.
	full := strings.TrimFunc(name(row.FirstName)+" "+name(row.LastName), pythonStripSpace)
	return map[string]any{"id": row.ID, "full_name": full, "is_minor": row.IsMinor}
}

func pythonStripSpace(r rune) bool {
	switch r {
	case ' ', '\t', '\n', '\r', '\v', '\f', 0x1c, 0x1d, 0x1e, 0x1f, 0x85, 0xa0, 0x1680, 0x2028, 0x2029, 0x202f, 0x205f, 0x3000:
		return true
	}
	return r >= 0x2000 && r <= 0x200a
}

func profileMiscIDs(rows []domain.ProfileAssociate) []string {
	ids := make([]string, len(rows))
	for i, row := range rows {
		ids[i] = row.ID
	}
	return ids
}

// CourseAssociates is profile_associates_course.
func (s *ProfileMisc) CourseAssociates(ctx context.Context, identity domain.ResolvedIdentity, uid, group string) (map[string]any, error) {
	parsed, err := paymentInfoParseUUID(uid)
	if err != nil {
		return nil, err
	}
	user, _, err := profileMiscRequestUser(identity)
	if err != nil {
		return nil, err
	}
	scope := profileMiscScope(identity, group)
	course := parsed.String()
	found, association, owner, err := s.reader.CourseAssociation(ctx, course, scope)
	if err != nil {
		return nil, err
	}
	if !found || association == nil || owner == nil {
		return nil, ErrProfileMiscServer // None.sport_association / None.user
	}
	associates, err := s.reader.Associates(ctx, []string{user, *owner}, false, scope)
	if err != nil {
		return nil, err
	}
	ids := profileMiscIDs(associates)
	subscribed, err := s.reader.CourseSubscribed(ctx, course, ids, scope)
	if err != nil {
		return nil, err
	}
	subscriptions, err := s.reader.UserSubscriptions(ctx, user, *association, ids, scope)
	if err != nil {
		return nil, err
	}
	in := map[string]bool{}
	for _, id := range subscribed {
		in[id] = true
	}
	yes, no := []map[string]any{}, []map[string]any{}
	for _, row := range associates {
		if in[row.ID] {
			yes = append(yes, profileAssociateEntry(row))
			continue
		}
		for _, item := range subscriptions {
			if item.AssociateID == row.ID {
				entry := profileAssociateEntry(row)
				entry["subscription_id"] = item.SubscriptionID
				no = append(no, entry)
				break
			}
		}
	}
	return map[string]any{"associates_subscribed": yes, "associates_unsubscribed": no, "full": len(no) == 0}, nil
}

// SportAssociates is profile_associates_sport_association.
func (s *ProfileMisc) SportAssociates(ctx context.Context, identity domain.ResolvedIdentity, uid, group string) (map[string]any, error) {
	parsed, err := paymentInfoParseUUID(uid)
	if err != nil {
		return nil, err
	}
	user, _, err := profileMiscRequestUser(identity)
	if err != nil {
		return nil, err
	}
	scope := profileMiscScope(identity, group)
	day, month, err := s.reader.BalanceStart(ctx, user)
	if err != nil {
		return nil, err
	}
	year, err := BalanceSheetRange(s.now(), day, month)
	if err != nil {
		return nil, ErrProfileMiscServer
	}
	associates, err := s.reader.Associates(ctx, []string{user}, true, scope)
	if err != nil {
		return nil, err
	}
	subscriptions, err := s.reader.SeasonSubscriptions(ctx, parsed.String(), profileMiscIDs(associates), year.From, year.To, scope)
	if err != nil {
		return nil, err
	}
	first := map[string]string{}
	for _, item := range subscriptions {
		first[item.AssociateID] = item.SubscriptionID
	}
	yes, no := []map[string]any{}, []map[string]any{}
	for _, row := range associates {
		entry := profileAssociateEntry(row)
		if id, ok := first[row.ID]; ok {
			entry["subscription_id"] = id
			yes = append(yes, entry)
		} else {
			no = append(no, entry)
		}
	}
	return map[string]any{"associates_subscribed": yes, "associates_unsubscribed": no}, nil
}

// Image is profile_image: User.objects.get(user_id=uid).avatar_image.
func (s *ProfileMisc) Image(ctx context.Context, uid string) (*string, error) {
	parsed, err := paymentInfoParseUUID(uid)
	if err != nil {
		return nil, err
	}
	found, avatar, err := s.reader.Avatar(ctx, parsed.String())
	if err != nil {
		return nil, err
	}
	if !found {
		return nil, ErrProfileMiscServer
	}
	return avatar, nil
}

// ---------------------------------------------------------------------------------------------
// onboarding/update
// ---------------------------------------------------------------------------------------------

var onboardingBranches = []string{"sport_association", "membership_data", "lead_data", "onboarding_data"}

// onboardingContains is `key in data` for the parsed request body; ok is false
// for the TypeError of a body that is not a container.
func onboardingContains(data any, key string) (bool, bool) {
	switch v := data.(type) {
	case domain.RequestObject:
		_, present := v.Lookup(key)
		return present, true
	case []any:
		for _, item := range v {
			if text, isText := item.(string); isText && text == key {
				return true, true
			}
		}
		return false, true
	case string:
		return strings.Contains(v, key), true
	}
	return false, false
}

// pythonSubscript is data[key] for a str key: the KeyError/TypeError text.
func pythonSubscript(data any, key string) (any, error) {
	switch v := data.(type) {
	case domain.RequestObject:
		value, present := v.Lookup(key)
		if !present {
			return nil, &OnboardingServerError{Message: dto.PythonStringRepr(key)}
		}
		return value, nil
	case string:
		return nil, &OnboardingServerError{Message: "string indices must be integers, not 'str'"}
	case []any:
		return nil, &OnboardingServerError{Message: "list indices must be integers or slices, not str"}
	}
	return nil, &OnboardingServerError{Message: "'" + pythonTypeNameOf(data) + "' object is not subscriptable"}
}

func pythonTypeNameOf(value any) string {
	switch v := value.(type) {
	case nil:
		return "NoneType"
	case bool:
		return "bool"
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return "float"
		}
		return "int"
	case string:
		return "str"
	case []any:
		return "list"
	}
	return "dict"
}

func onboardingText(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

// Onboarding is onboarding_update after authorization and method dispatch.
func (s *ProfileMisc) Onboarding(ctx context.Context, identity domain.ResolvedIdentity, body any, audit domain.LoginAuditContext) error {
	if dto.ProfileSettingsHasUpload(body) {
		return ErrOnboardingNotPorted
	}
	// The logging statement's membership tests precede the view's try.
	branch := ""
	for _, key := range onboardingBranches {
		present, ok := onboardingContains(body, key)
		if !ok {
			return ErrProfileMiscServer
		}
		if present && branch == "" {
			branch = key
		}
	}
	user, role, err := profileMiscRequestUser(identity)
	if err != nil {
		return err
	}
	if role != domain.RoleAssociation && role != domain.RoleCollaborator {
		return ErrOnboardingNotAssociation
	}
	if identity.Actor == nil {
		return domain.ErrInvalidScopedRequest
	}
	auditContext := paymentCategoryAuditContext(identity, audit)
	if branch == "" {
		return nil
	}
	data, err := pythonSubscript(body, branch)
	if err != nil {
		return err
	}
	switch branch {
	case "sport_association":
		return s.onboardingAssociation(ctx, user, data, auditContext)
	case "membership_data":
		return s.onboardingMembership(ctx, user, data, auditContext)
	case "lead_data":
		return s.onboardingLead(ctx, user, data, auditContext)
	}
	return s.onboardingFlags(ctx, user, data, auditContext)
}

func (s *ProfileMisc) association(ctx context.Context, tx domain.OnboardingTransaction, user string) (*domain.OnboardingAssociation, error) {
	association, err := tx.Association(ctx, user)
	if err != nil {
		return nil, err
	}
	if association == nil {
		return nil, &OnboardingServerError{Message: "User has no sportassociation."}
	}
	if association.Deleted {
		// auditlog's default manager cannot load a deleted association's old row.
		return nil, ErrOnboardingNotPorted
	}
	return association, nil
}

// applyOnboardingAssociation is serializer.save(): the instance with the
// validated fields, and auditlog's str() pairs against the stored row.
func applyOnboardingAssociation(stored domain.OnboardingAssociation, input dto.OnboardingAssociationInput) (domain.OnboardingAssociation, map[string][2]string) {
	next := stored
	if input.Denomination != nil {
		next.Denomination = *input.Denomination
	}
	if input.TaxCode != nil {
		next.TaxCode = *input.TaxCode
	}
	if input.Logo != nil {
		next.Logo = *input.Logo
	}
	if input.Sport != nil {
		next.Sport = *input.Sport
	}
	if input.SubscriptionFee != nil && *input.SubscriptionFee != nil {
		next.SubscriptionFee = **input.SubscriptionFee
	}
	if input.MembershipFee != nil {
		next.MembershipFee = *input.MembershipFee
	}
	return next, onboardingAssociationChanges(stored, next.Denomination, next.TaxCode, next.Logo, next.Sport, next.SubscriptionFee,
		onboardingText(next.MembershipFee))
}

func onboardingAssociationChanges(stored domain.OnboardingAssociation, denomination, tax string, logo, sport *string, subscription, membership string) map[string][2]string {
	changes := map[string][2]string{}
	add := func(name, old, new string) {
		if old != new {
			changes[name] = [2]string{old, new}
		}
	}
	add("denomination", stored.Denomination, denomination)
	add("tax_code", stored.TaxCode, tax)
	add("logo", onboardingText(stored.Logo), onboardingText(logo))
	add("sport", onboardingText(stored.Sport), onboardingText(sport))
	add("subscription_fee", stored.SubscriptionFee, subscription)
	add("membership_fee", onboardingText(stored.MembershipFee), membership)
	return changes
}

func (s *ProfileMisc) associationAudit(ctx context.Context, tx domain.OnboardingTransaction, id string, changes map[string][2]string, auditContext domain.LoginAuditContext) error {
	if len(changes) == 0 {
		return nil
	}
	return tx.AppendAudit(ctx, domain.ProfileMiscAudit{Model: "sportassociation", Key: id, Representation: "SportAssociation object (" + id + ")",
		Changes: changes, Context: auditContext, AssociationID: &id, ResolutionPath: "self", At: s.now()})
}

func (s *ProfileMisc) onboardingAssociation(ctx context.Context, user string, data any, auditContext domain.LoginAuditContext) error {
	tx, err := s.store.BeginOnboarding(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	stored, err := s.association(ctx, tx, user)
	if err != nil {
		return err
	}
	input, invalid := dto.ParseOnboardingAssociation(data)
	if invalid != nil {
		return &OnboardingValidationError{Fields: invalid}
	}
	next, changes := applyOnboardingAssociation(*stored, input)
	if err = s.associationAudit(ctx, tx, stored.ID, changes, auditContext); err != nil {
		return err
	}
	if err = tx.SaveAssociation(ctx, next); err != nil {
		return err
	}
	return tx.Commit()
}

// onboardingUserAudit is user.save()'s auditlog entry: the user resolver,
// then the actor's association.
func (s *ProfileMisc) onboardingUserAudit(ctx context.Context, tx domain.OnboardingTransaction, user domain.OnboardingUser, changes map[string][2]string, auditContext domain.LoginAuditContext) error {
	if len(changes) == 0 {
		return nil
	}
	association, err := tx.AssociationOf(ctx, user.ID)
	if err != nil {
		return err
	}
	path := "user.sport_association"
	if association == nil {
		if association, err = tx.AssociationOf(ctx, *auditContext.ActorID); err != nil {
			return err
		}
		path = "actor.sport_association"
	}
	return tx.AppendAudit(ctx, domain.ProfileMiscAudit{Model: "user", Key: user.ID, Representation: "name: " + user.FirstName + " " + user.LastName,
		Changes: changes, Context: auditContext, AssociationID: association, ResolutionPath: path, At: s.now()})
}

func (s *ProfileMisc) onboardingMembership(ctx context.Context, user string, data any, auditContext domain.LoginAuditContext) error {
	tx, err := s.store.BeginOnboarding(ctx) // transaction.atomic()
	if err != nil {
		return err
	}
	defer tx.Rollback()
	stored, err := s.association(ctx, tx, user)
	if err != nil {
		return err
	}
	config, err := tx.FreshConfiguration(ctx, stored.ID)
	if err != nil {
		return err
	}
	if config != nil && config.OwnerID != user {
		config = nil
	}
	if config != nil && config.Completed == nil {
		if details := dto.FreshMembershipErrors(data); len(details) > 0 {
			return &OnboardingFreshError{Details: details}
		}
	}
	input, invalid := dto.ParseOnboardingAssociation(data)
	if invalid != nil {
		return &OnboardingValidationError{Fields: invalid}
	}
	next, changes := applyOnboardingAssociation(*stored, input)
	if err = s.associationAudit(ctx, tx, stored.ID, changes, auditContext); err != nil {
		return err
	}
	if err = tx.SaveAssociation(ctx, next); err != nil {
		return err
	}
	membership := data.(domain.RequestObject) // the serializer accepted a dict
	person, err := tx.User(ctx, user)
	if err != nil {
		return err
	}
	type assigned struct {
		name  string
		value any
	}
	values := []assigned{{"balance_sheet_year", json.Number("4")}}
	for _, section := range []struct{ key, day, month string }{
		{"season", "subscription_start_day", "subscription_start_month"}, {"fiscal", "balance_sheet_start_day", "balance_sheet_start_month"}} {
		raw, present := membership.Lookup(section.key)
		if !present {
			continue
		}
		day, err := pythonSubscript(raw, "day")
		if err != nil {
			return err
		}
		month, err := pythonSubscript(raw, "month")
		if err != nil {
			return err
		}
		values = append(values, assigned{section.day, day}, assigned{section.month, month})
	}
	// float() of the raw fees; the second save audits str(float) against the stored decimal.
	subscription, membershipFee := next.SubscriptionFee, onboardingText(next.MembershipFee)
	for _, fee := range []struct {
		name   string
		target *string
	}{{"membership_fee", &membershipFee}, {"subscription_fee", &subscription}} {
		raw, present := membership.Lookup(fee.name)
		if !present || raw == nil {
			continue
		}
		repr, ok := dto.PythonFloatRepr(raw)
		if !ok {
			return ErrOnboardingNotPorted
		}
		*fee.target = repr
	}
	floatChanges := onboardingAssociationChanges(next, next.Denomination, next.TaxCode, next.Logo, next.Sport, subscription, membershipFee)
	if err = s.associationAudit(ctx, tx, stored.ID, floatChanges, auditContext); err != nil {
		return err
	}
	// user.save(): auditlog compares str() of the assigned values, then the
	// columns convert them in model field order.
	saved := person
	targets := map[string]*int64{"balance_sheet_year": &saved.BalanceSheetYear, "balance_sheet_start_day": &saved.BalanceSheetStartDay,
		"balance_sheet_start_month": &saved.BalanceSheetStartMonth, "subscription_start_day": &saved.SubscriptionStartDay,
		"subscription_start_month": &saved.SubscriptionStartMonth}
	olds := map[string]int64{"balance_sheet_year": person.BalanceSheetYear, "balance_sheet_start_day": person.BalanceSheetStartDay,
		"balance_sheet_start_month": person.BalanceSheetStartMonth, "subscription_start_day": person.SubscriptionStartDay,
		"subscription_start_month": person.SubscriptionStartMonth}
	latest := map[string]any{}
	for _, item := range values {
		latest[item.name] = item.value
	}
	changes = map[string][2]string{}
	for name, value := range latest {
		if old, new := pythonIntText(olds[name]), dto.PythonStr(value); old != new {
			changes[name] = [2]string{old, new}
		}
	}
	for _, name := range []string{"balance_sheet_year", "balance_sheet_start_day", "balance_sheet_start_month", "subscription_start_day",
		"subscription_start_month"} {
		value, present := latest[name]
		if !present {
			continue
		}
		number, invalid, supported := dto.PythonPositiveSmallInt(value)
		if !supported {
			return ErrOnboardingNotPorted
		}
		if invalid {
			// The audit entry and the earlier saves roll back with the block.
			return &OnboardingServerError{Message: "Field '" + name + "' expected a number but got " + dto.PythonStringRepr(value.(string)) + "."}
		}
		*targets[name] = number
	}
	if err = s.onboardingUserAudit(ctx, tx, person, changes, auditContext); err != nil {
		return err
	}
	if err = tx.SaveUser(ctx, saved); err != nil {
		return err
	}
	if config != nil && config.Completed == nil {
		if err = tx.CompleteConfiguration(ctx, config.ID, s.now().UTC()); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func pythonIntText(value int64) string { return strconv.FormatInt(value, 10) }

// onboardingLeadValue is CharField.to_python (str()) for the stored lead
// values; containers and values the column rejects are refused.
func onboardingLeadValue(value any, maximum int) (*string, error) {
	switch value.(type) {
	case nil:
		return nil, nil
	case string, bool, json.Number:
	default:
		return nil, ErrOnboardingNotPorted
	}
	text := dto.PythonStr(value)
	if utf8.RuneCountInString(text) > maximum || strings.ContainsRune(text, 0) || !utf8.ValidString(text) {
		return nil, ErrOnboardingNotPorted
	}
	return &text, nil
}

func (s *ProfileMisc) onboardingLead(ctx context.Context, user string, data any, auditContext domain.LoginAuditContext) error {
	values := map[string]any{}
	for _, name := range []string{"lead_sport_association_role", "lead_sport_association_size", "lead_sport_market_channel"} {
		value, err := pythonSubscript(data, name)
		if err != nil {
			return err
		}
		values[name] = value
	}
	if object, ok := data.(domain.RequestObject); ok {
		if phone, present := object.Lookup("phone"); present {
			values["phone"] = phone
		}
	}
	tx, err := s.store.BeginOnboarding(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	person, err := tx.User(ctx, user)
	if err != nil {
		return err
	}
	saved := person
	fields := map[string]struct {
		target  **string
		old     *string
		maximum int
	}{"lead_sport_association_role": {&saved.Role, person.Role, 150}, "lead_sport_association_size": {&saved.Size, person.Size, 150},
		"lead_sport_market_channel": {&saved.Channel, person.Channel, 150}, "phone": {&saved.Phone, person.Phone, 20}}
	changes := map[string][2]string{}
	for name, value := range values {
		field := fields[name]
		stored, err := onboardingLeadValue(value, field.maximum)
		if err != nil {
			return err
		}
		if old, new := onboardingText(field.old), dto.PythonStr(value); old != new {
			changes[name] = [2]string{old, new}
		}
		*field.target = stored
	}
	if err = s.onboardingUserAudit(ctx, tx, person, changes, auditContext); err != nil {
		return err
	}
	if err = tx.SaveUser(ctx, saved); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *ProfileMisc) onboardingFlags(ctx context.Context, user string, data any, auditContext domain.LoginAuditContext) error {
	tx, err := s.store.BeginOnboarding(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	rows, err := tx.Flags(ctx, user)
	if err != nil {
		return err
	}
	switch {
	case len(rows) == 0:
		return &OnboardingServerError{Message: "UsersOnboarding matching query does not exist."}
	case len(rows) > 1:
		return &OnboardingServerError{Message: "get() returned more than one UsersOnboarding -- it returned " + strconv.Itoa(len(rows)) + "!"}
	}
	flags, invalid := dto.ParseOnboardingFlags(data)
	if invalid != nil {
		return &OnboardingValidationError{Fields: invalid}
	}
	row := rows[0]
	saved := domain.OnboardingFlags{ID: row.ID, Flags: map[string]bool{}}
	changes := map[string][2]string{}
	for _, name := range dto.OnboardingFlagFields {
		saved.Flags[name] = row.Flags[name]
		if value, ok := flags[name]; ok {
			if value != row.Flags[name] {
				changes[name] = [2]string{pythonBool(row.Flags[name]), pythonBool(value)}
			}
			saved.Flags[name] = value
		}
	}
	if len(changes) > 0 {
		// No sport_association attribute or change: the actor's association.
		association, err := tx.AssociationOf(ctx, *auditContext.ActorID)
		if err != nil {
			return err
		}
		if err = tx.AppendAudit(ctx, domain.ProfileMiscAudit{Model: "usersonboarding", Key: row.ID,
			Representation: "UsersOnboarding object (" + row.ID + ")", Changes: changes, Context: auditContext, AssociationID: association,
			ResolutionPath: "actor.sport_association", At: s.now()}); err != nil {
			return err
		}
	}
	if err = tx.SaveFlags(ctx, saved); err != nil {
		return err
	}
	return tx.Commit()
}
