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

// ProfileSettingsCards is the membership-card get-or-create shared with the
// login response (its own committed transaction, like Django's autocommit).
type ProfileSettingsCards interface {
	ForLogin(ctx context.Context, account domain.Account, association string, audit domain.LoginAuditContext) (*dto.MembershipCardConfiguration, error)
}

// ProfileSettings ports profile_settings and profile_integrations.
type ProfileSettings struct {
	store domain.ProfileSettingsStore
	cards ProfileSettingsCards
	now   func() time.Time
}

func NewProfileSettings(store domain.ProfileSettingsStore, cards ProfileSettingsCards, now func() time.Time) *ProfileSettings {
	return &ProfileSettings{store: store, cards: cards, now: now}
}

// ProfileSettingsValidationError is UserSettingsSerializer's 400 field map.
type ProfileSettingsValidationError struct{ Fields map[string]any }

func (e *ProfileSettingsValidationError) Error() string { return "invalid profile settings input" }

var (
	// ErrProfileSettingsForeignCategory is tenant_scope's PermissionDenied
	// raised while validating a relation to another association's category.
	ErrProfileSettingsForeignCategory = errors.New("profile settings category of another association")
	// ErrProfileSettingsServer is a Python exception Django raises after the
	// writes made so far (KeyError, TypeError, ValueError, ValidationError,
	// IntegrityError, DataError): the HTML 500 page.
	ErrProfileSettingsServer = errors.New("profile settings server error")
	// ErrProfileSettingsUpload refuses a multipart file in the request data
	// before any write (not ported).
	ErrProfileSettingsUpload = refusal.New("profile settings upload is not ported")
)

// ProfileIntegrations is the PATCH response: the assigned request values.
type ProfileIntegrations struct {
	URL, Enabled any
}

// owner is request.user and user.sport_association: athletes are refused,
// collaborators were already mapped to their owner, a missing (or deleted)
// association is SportAssociation.DoesNotExist (500).
func (s *ProfileSettings) owner(ctx context.Context, identity domain.ResolvedIdentity) (string, string, error) {
	if identity.Actor == nil || identity.ScopeUser == nil || identity.ScopeUser.ID == "" {
		return "", "", domain.ErrInvalidScopedRequest
	}
	user := identity.ScopeUser
	if user.Role == domain.RoleAthlete {
		return "", "", domain.ErrProfileNotAssociation
	}
	owner := user.ID
	if user.Role == domain.RoleCollaborator {
		if user.ConnectedUserID == nil {
			return "", "", domain.ErrAssociationNotFound
		}
		owner = *user.ConnectedUserID
	}
	tx, err := s.store.BeginProfileSettings(ctx)
	if err != nil {
		return "", "", err
	}
	defer tx.Rollback()
	association, err := tx.OwnerAssociation(ctx, owner)
	if err != nil {
		return "", "", err
	}
	return owner, association, tx.Commit()
}

// card is the configuration lookup at the top of profile_settings: the
// first one, created (with its audit) when missing.
func (s *ProfileSettings) card(ctx context.Context, identity domain.ResolvedIdentity, owner, association string,
	audit domain.LoginAuditContext) (*dto.MembershipCardConfiguration, error) {
	return s.cards.ForLogin(ctx, domain.Account{ID: owner, Role: domain.RoleAssociation}, association,
		paymentCategoryAuditContext(identity, audit))
}

// Settings is profile_settings GET.
func (s *ProfileSettings) Settings(ctx context.Context, identity domain.ResolvedIdentity, audit domain.LoginAuditContext) (map[string]any, error) {
	owner, association, err := s.owner(ctx, identity)
	if err != nil {
		return nil, err
	}
	card, err := s.card(ctx, identity, owner, association, audit)
	if err != nil {
		return nil, err
	}
	tx, err := s.store.BeginProfileSettings(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	user, err := tx.User(ctx, owner, false)
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	year, err := profileBalanceYear(s.now(), user.BalanceSheetStartDay, user.BalanceSheetStartMonth)
	if err != nil {
		return nil, err
	}
	v := user.ProfileSettings
	settings := map[string]any{
		"enumerate_invoices": v.EnumerateInvoices, "online_payments": v.OnlinePayments,
		"balance_sheet_year": strconv.FormatInt(v.BalanceSheetYear, 10), "balance_sheet_start_day": v.BalanceSheetStartDay,
		"balance_sheet_start_month": v.BalanceSheetStartMonth, "temporary_invoice_deletion": v.TemporaryInvoiceDeletion,
		"auto_archive": v.AutoArchive, "auto_mark_attendance": v.AutoMarkAttendance, "current_year": year,
		"payment_date_equal_invoice_date": v.PaymentDateEqualInvoiceDate, "starting_number_invoices": v.StartingNumberInvoices,
		"auto_paid_payment": v.AutoPaidPayment, "full_installments_plan": v.FullInstallmentsPlan, "show_zero_payments": v.ShowZeroPayments,
		"table_settings": user.Tables, "dark_mode": v.DarkMode,
		"subscription_duration_equal_sport_year": v.SubscriptionDurationEqualSportYear, "subscription_duration": v.SubscriptionDuration,
		"membership_duration": v.MembershipDuration, "membership_starting_number": v.MembershipStartingNumber,
		"default_membership_type": v.DefaultMembershipType, "subscription_start_day": v.SubscriptionStartDay,
		"subscription_start_month": v.SubscriptionStartMonth, "custom_end_date": v.CustomEndDate,
		"subscription_end_day": v.SubscriptionEndDay, "subscription_end_month": v.SubscriptionEndMonth,
		"disable_account_creation": v.DisableAccountCreation, "force_account_creation": v.ForceAccountCreation,
		"medical_certificate_notifications": v.MedicalCertificateNotifications, "hide_category_name": v.HideCategoryName,
		"default_payment_category": v.DefaultPaymentCategory, "default_payment_category_courses": v.DefaultPaymentCategoryCourses,
		"membership_card_configuration": map[string]any{"emit_only_on_approval": card.EmitOnlyOnApproval,
			"customized_template": card.CustomizedTemplate},
	}
	return map[string]any{"settings": settings}, nil
}

// profileBalanceYear is BalanceSheetData.get_range_from_year_and_starting_date
// (datetime.now(), day, month)[0].year with TIME_ZONE='UTC'. Out-of-range
// values and dates datetime() rejects in this or the previous year are a
// ValueError (500).
func profileBalanceYear(now time.Time, day, month int64) (int, error) {
	from, _, ok := balanceSheetRange(now, day, month)
	if !ok {
		return 0, ErrProfileSettingsServer
	}
	return from.Year(), nil
}

// UpdateSettings is profile_settings POST.
func (s *ProfileSettings) UpdateSettings(ctx context.Context, identity domain.ResolvedIdentity, body any, form bool,
	audit domain.LoginAuditContext) error {
	owner, association, err := s.owner(ctx, identity)
	if err != nil {
		return err
	}
	if dto.ProfileSettingsHasUpload(body) {
		return ErrProfileSettingsUpload
	}
	if _, err = s.card(ctx, identity, owner, association, audit); err != nil {
		return err
	}
	input, invalid := dto.ParseProfileSettings(body, form)
	if input == nil {
		return &ProfileSettingsValidationError{Fields: invalid}
	}
	if invalid == nil {
		invalid = map[string]any{}
	}
	if err = s.relations(ctx, association, input, invalid); err != nil {
		return err
	}
	if len(invalid) > 0 {
		return &ProfileSettingsValidationError{Fields: invalid}
	}
	for _, field := range dto.ProfileSettingsFields {
		if _, present := input[field.Name]; !present {
			// settings.validated_data[...]: KeyError before any save.
			return ErrProfileSettingsServer
		}
	}
	auditContext := paymentCategoryAuditContext(identity, audit)
	if err = s.saveUser(ctx, owner, association, input, auditContext); err != nil {
		return err
	}
	// request.data['membership_card_configuration'][...] after user.save().
	object, _ := body.(domain.RequestObject)
	card, present := object.Lookup("membership_card_configuration")
	nested, isObject := card.(domain.RequestObject)
	if form || !present || !isObject {
		return ErrProfileSettingsServer
	}
	emit, hasEmit := nested.Lookup("emit_only_on_approval")
	template, hasTemplate := nested.Lookup("customized_template")
	if !hasEmit || !hasTemplate {
		return ErrProfileSettingsServer
	}
	return s.saveCard(ctx, association, emit, template, auditContext)
}

// relations looks up both PaymentCategory relations in field order; a
// category of another association raises PermissionDenied, discarding the
// field errors (current_association() is the owner's association).
func (s *ProfileSettings) relations(ctx context.Context, association string, input dto.ProfileSettingsInput, invalid map[string]any) error {
	tx, err := s.store.BeginProfileSettings(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	for _, name := range []string{"default_payment_category", "default_payment_category_courses"} {
		relation, ok := input[name].(dto.ProfileSettingsRelation)
		if !ok || relation.Null {
			continue
		}
		exists, owner, err := tx.PaymentCategory(ctx, relation.ID)
		if err != nil {
			return err
		}
		if !exists {
			delete(input, name)
			invalid[name] = []string{"related-does-not-exist:" + relation.Raw}
			continue
		}
		if owner != nil && *owner != association {
			return ErrProfileSettingsForeignCategory
		}
	}
	return tx.Commit()
}

func profileSettingsValues(current domain.ProfileSettings, input dto.ProfileSettingsInput) domain.ProfileSettings {
	v := current
	booleans := map[string]*bool{"enumerate_invoices": &v.EnumerateInvoices, "online_payments": &v.OnlinePayments,
		"temporary_invoice_deletion": &v.TemporaryInvoiceDeletion, "auto_archive": &v.AutoArchive,
		"auto_mark_attendance": &v.AutoMarkAttendance, "payment_date_equal_invoice_date": &v.PaymentDateEqualInvoiceDate,
		"auto_paid_payment": &v.AutoPaidPayment, "full_installments_plan": &v.FullInstallmentsPlan,
		"show_zero_payments": &v.ShowZeroPayments, "dark_mode": &v.DarkMode,
		"medical_certificate_notifications": &v.MedicalCertificateNotifications, "hide_category_name": &v.HideCategoryName,
		"subscription_duration_equal_sport_year": &v.SubscriptionDurationEqualSportYear, "custom_end_date": &v.CustomEndDate,
		"disable_account_creation": &v.DisableAccountCreation, "force_account_creation": &v.ForceAccountCreation}
	integers := map[string]*int64{"balance_sheet_year": &v.BalanceSheetYear, "balance_sheet_start_day": &v.BalanceSheetStartDay,
		"balance_sheet_start_month": &v.BalanceSheetStartMonth, "starting_number_invoices": &v.StartingNumberInvoices,
		"subscription_duration": &v.SubscriptionDuration, "membership_duration": &v.MembershipDuration,
		"membership_starting_number": &v.MembershipStartingNumber, "subscription_start_day": &v.SubscriptionStartDay,
		"subscription_start_month": &v.SubscriptionStartMonth, "subscription_end_day": &v.SubscriptionEndDay,
		"subscription_end_month": &v.SubscriptionEndMonth}
	relations := map[string]**string{"default_payment_category": &v.DefaultPaymentCategory,
		"default_payment_category_courses": &v.DefaultPaymentCategoryCourses}
	for name, value := range input {
		switch typed := value.(type) {
		case bool:
			*booleans[name] = typed
		case int64:
			*integers[name] = typed
		case *string:
			v.DefaultMembershipType = typed
		case dto.ProfileSettingsRelation:
			if typed.Null {
				*relations[name] = nil
			} else {
				id := typed.ID
				*relations[name] = &id
			}
		}
	}
	return v
}

// profileSettingsChanges is auditlog's diff of the 30 fields: str(old) and
// str(new), None for nulls.
func profileSettingsChanges(old, new domain.ProfileSettings) map[string][2]string {
	text := func(value any) string {
		switch v := value.(type) {
		case bool:
			return pythonBool(v)
		case int64:
			return strconv.FormatInt(v, 10)
		case *string:
			if v == nil {
				return "None"
			}
			return *v
		}
		panic("unsupported profile setting")
	}
	pairs := func(v domain.ProfileSettings) []any {
		return []any{v.EnumerateInvoices, v.OnlinePayments, v.BalanceSheetYear, v.BalanceSheetStartDay, v.BalanceSheetStartMonth,
			v.TemporaryInvoiceDeletion, v.AutoArchive, v.AutoMarkAttendance, v.PaymentDateEqualInvoiceDate, v.StartingNumberInvoices,
			v.AutoPaidPayment, v.FullInstallmentsPlan, v.ShowZeroPayments, v.DarkMode, v.MedicalCertificateNotifications,
			v.HideCategoryName, v.SubscriptionDurationEqualSportYear, v.SubscriptionDuration, v.MembershipDuration,
			v.MembershipStartingNumber, v.DefaultMembershipType, v.SubscriptionStartDay, v.SubscriptionStartMonth, v.CustomEndDate,
			v.SubscriptionEndDay, v.SubscriptionEndMonth, v.DisableAccountCreation, v.ForceAccountCreation, v.DefaultPaymentCategory,
			v.DefaultPaymentCategoryCourses}
	}
	before, after := pairs(old), pairs(new)
	changes := map[string][2]string{}
	for index, field := range dto.ProfileSettingsFields {
		if left, right := text(before[index]), text(after[index]); left != right {
			changes[field.Name] = [2]string{left, right}
		}
	}
	return changes
}

// saveUser is user.save(): the changed fields are audited (resolved through
// the owner's association) and the 30 columns written.
func (s *ProfileSettings) saveUser(ctx context.Context, owner, association string, input dto.ProfileSettingsInput,
	audit domain.LoginAuditContext) error {
	tx, err := s.store.BeginProfileSettings(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	user, err := tx.User(ctx, owner, true)
	if err != nil {
		return err
	}
	values := profileSettingsValues(user.ProfileSettings, input)
	if changes := profileSettingsChanges(user.ProfileSettings, values); len(changes) > 0 {
		if err = tx.AppendAudit(ctx, domain.ProfileSettingsAudit{Model: "user", Key: owner,
			Representation: "name: " + user.FirstName + " " + user.LastName, Action: 1, Changes: changes,
			AssociationID: association, ResolutionPath: "user.sport_association", Context: audit, At: s.auditTime()}); err != nil {
			return err
		}
	}
	if err = tx.SaveSettings(ctx, owner, values); err != nil {
		return err
	}
	return tx.Commit()
}

func (s *ProfileSettings) auditTime() time.Time { return s.now().UTC().Truncate(time.Microsecond) }

// saveCard is the configuration save: auditlog writes the entry in pre_save,
// then the model BooleanField's to_python (or the NOT NULL column) may reject
// the value, keeping the entry and not the row.
func (s *ProfileSettings) saveCard(ctx context.Context, association string, emit, template any, audit domain.LoginAuditContext) error {
	tx, err := s.store.BeginProfileSettings(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	card, err := tx.FirstCard(ctx, association)
	if err != nil {
		return err
	}
	if card == nil {
		return domain.ErrProfileCardMissing
	}
	stored, err := dto.ParseReferenceJSON(card.CustomizedTemplate)
	if err != nil {
		return err
	}
	before, err := profileJSONText(ctx, stored)
	if err != nil {
		return err
	}
	after, err := profileJSONText(ctx, template)
	if err != nil {
		return err
	}
	changes := map[string][2]string{}
	if old, new := pythonBool(card.EmitOnlyOnApproval), dto.PythonStr(emit); old != new {
		changes["emit_only_on_approval"] = [2]string{old, new}
	}
	if before != after {
		changes["customized_template"] = [2]string{before, after}
	}
	if len(changes) > 0 {
		if err = tx.AppendAudit(ctx, domain.ProfileSettingsAudit{Model: "sportassociationmembershipcardconfiguration", Key: card.ID,
			Representation: "SportAssociationMembershipCardConfiguration object (" + card.ID + ")", Action: 1, Changes: changes,
			AssociationID: association, ResolutionPath: "direct", Context: audit, At: s.auditTime()}); err != nil {
			return err
		}
	}
	parsed, err := profileBoolean(emit)
	if err != nil {
		if err = tx.Commit(); err != nil {
			return err
		}
		return ErrProfileSettingsServer
	}
	var raw json.RawMessage
	if template != nil {
		raw = json.RawMessage(after)
	}
	if err = tx.SaveCard(ctx, card.ID, parsed, raw); err != nil {
		return err
	}
	return tx.Commit()
}

// Integrations is profile_integrations GET.
func (s *ProfileSettings) Integrations(ctx context.Context, identity domain.ResolvedIdentity) (ProfileIntegrations, error) {
	_, association, err := s.owner(ctx, identity)
	if err != nil {
		return ProfileIntegrations{}, err
	}
	tx, err := s.store.BeginProfileSettings(ctx)
	if err != nil {
		return ProfileIntegrations{}, err
	}
	defer tx.Rollback()
	review, err := tx.Review(ctx, association, false)
	if err != nil {
		return ProfileIntegrations{}, err
	}
	return ProfileIntegrations{URL: review.URL, Enabled: review.Enabled}, tx.Commit()
}

// UpdateIntegrations is profile_integrations PATCH: request.data.get(...)
// with the stored values as defaults, assigned without validation. auditlog
// compares str() values in pre_save; CharField.to_python is str(), the model
// BooleanField and the varchar(255) NOT NULL columns may then reject the
// values (500, the entry kept).
func (s *ProfileSettings) UpdateIntegrations(ctx context.Context, identity domain.ResolvedIdentity, body any,
	audit domain.LoginAuditContext) (ProfileIntegrations, error) {
	_, association, err := s.owner(ctx, identity)
	if err != nil {
		return ProfileIntegrations{}, err
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		// request.data.get on a list, scalar or None: AttributeError.
		return ProfileIntegrations{}, ErrProfileSettingsServer
	}
	tx, err := s.store.BeginProfileSettings(ctx)
	if err != nil {
		return ProfileIntegrations{}, err
	}
	defer tx.Rollback()
	review, err := tx.Review(ctx, association, true)
	if err != nil {
		return ProfileIntegrations{}, err
	}
	var url, enabled any = nil, review.Enabled
	if review.URL != nil {
		url = *review.URL
	}
	if value, present := object.Lookup("review_url"); present {
		url = value
	}
	if value, present := object.Lookup("review_url_enabled"); present {
		enabled = value
	}
	// A file part is the QueryDict value: str() is its name and the response
	// renders it as its lines (File.__iter__), which needs the whole content.
	var echoed any = url
	var echoErr error
	if upload, isUpload := url.(domain.RequestUpload); isUpload {
		echoed, echoErr = profileUploadLines(upload)
		if echoErr != nil && !upload.Whole {
			return ProfileIntegrations{}, ErrProfileSettingsUpload
		}
	}
	if upload, isUpload := enabled.(domain.RequestUpload); isUpload && upload.Temp {
		return ProfileIntegrations{}, ErrProfileSettingsUpload
	}
	changes := map[string][2]string{}
	oldURL := "None"
	if review.URL != nil {
		oldURL = *review.URL
	}
	if new := profileText(url); new != oldURL {
		changes["review_url"] = [2]string{oldURL, new}
	}
	if old, new := pythonBool(review.Enabled), profileText(enabled); old != new {
		changes["review_url_enabled"] = [2]string{old, new}
	}
	if len(changes) > 0 {
		if err = tx.AppendAudit(ctx, domain.ProfileSettingsAudit{Model: "sportassociation", Key: association,
			Representation: "SportAssociation object (" + association + ")", Action: 1, Changes: changes,
			AssociationID: association, ResolutionPath: "self", Context: paymentCategoryAuditContext(identity, audit),
			At: s.auditTime()}); err != nil {
			return ProfileIntegrations{}, err
		}
	}
	var storedURL *string
	if url != nil {
		text := profileText(url)
		storedURL = &text
	}
	parsed, boolErr := profileBoolean(enabled)
	if boolErr != nil || storedURL != nil && (utf8.RuneCountInString(*storedURL) > 255 || containsNUL(*storedURL)) {
		if err = tx.Commit(); err != nil {
			return ProfileIntegrations{}, err
		}
		return ProfileIntegrations{}, ErrProfileSettingsServer
	}
	if err = tx.SaveReview(ctx, association, storedURL, parsed); err != nil {
		return ProfileIntegrations{}, err
	}
	if err = tx.Commit(); err != nil {
		return ProfileIntegrations{}, err
	}
	if echoErr != nil {
		// The renderer cannot encode the file's bytes: the data is saved, the answer is the 500 page.
		return ProfileIntegrations{}, ErrProfileSettingsServer
	}
	return ProfileIntegrations{URL: echoed, Enabled: enabled}, nil
}

// profileText is str(value) for a QueryDict value: a file part is its name.
func profileText(value any) string {
	if upload, ok := value.(domain.RequestUpload); ok {
		return upload.Name
	}
	return dto.PythonStr(value)
}

// profileUploadLines is tuple(file): the lines of File.__iter__ (bytes
// splitlines(True) over the first chunk) decoded as UTF-8 by DRF's encoder.
func profileUploadLines(upload domain.RequestUpload) ([]string, error) {
	if !upload.Whole {
		return nil, ErrProfileSettingsUpload
	}
	lines := []string{}
	data := upload.Content
	for len(data) > 0 {
		end := strings.IndexAny(data, "\r\n")
		if end < 0 {
			end = len(data)
		} else if data[end] == '\r' && end+1 < len(data) && data[end+1] == '\n' {
			end += 2
		} else {
			end++
		}
		line := data[:end]
		if !utf8.ValidString(line) {
			return nil, ErrProfileSettingsServer
		}
		lines = append(lines, line)
		data = data[end:]
	}
	return lines, nil
}

func containsNUL(text string) bool {
	for _, r := range text {
		if r == 0 {
			return true
		}
	}
	return false
}
