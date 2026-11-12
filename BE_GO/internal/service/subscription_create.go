package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"math/big"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

// subscriptionCreateAfterCommitTimeout bounds the on_commit work (tasks and the
// notification); it does not follow the request's cancellation.
const subscriptionCreateAfterCommitTimeout = 10 * time.Second

// SubscriptionCreateResult is the view's {"status": "success", ...} data.
type SubscriptionCreateResult struct {
	PaymentID *string
	// Amount is the JSON number: repr() of the payment's float amount, or 0.
	Amount string
	// Family marks the family wizard's answer; Payments are its serialized payments.
	Family   bool
	Payments []map[string]any
}

// SubscriptionCreateRequest is subscription_add's input after admission.
type SubscriptionCreateRequest struct {
	Body          any
	Form          bool
	Mode          *string // request.GET.get('m')
	Authorization *string // request.headers.get('authorization')
	Group         string
	// GroupID is the canonical X-Group-Id (nil: no header).
	GroupID *string
	// GroupInvalid marks a header the group-aware managers cannot parse: their
	// queries run unfiltered and the first create through a manager fails.
	GroupInvalid bool
	Audit        domain.LoginAuditContext
	// Language is the request's locale (the welcome e-mail).
	Language string
	// StorageKeys collects the objects stored during the request (cleanup_storage_keys on failure).
	StorageKeys *[]string
	// Anonymous marks a request without credentials (request.subscription_requester is None).
	Anonymous bool
	// AthleteRequest is is_athlete_request: an account other than an association naming one.
	AthleteRequest bool
	// RequesterID is create_subscription's requester when it is not request.user (a member
	// account's renewal runs as the association's owner).
	RequesterID string
}

// SubscriptionCreates ports subscription_add and create_subscription
// (subscription-create.md).
type SubscriptionCreates struct {
	store         domain.SubscriptionCreateStore
	tasks         celery.Publisher
	notifications *SubscriptionNotifier
	now           func() time.Time
	logger        *slog.Logger
	accounts      *SubscriptionCreateAccounts
	signatures    *SubscriptionSigns
	placeholders  *SubscriptionPlaceholders
}

// WithPlaceholders enables the placeholder certificate of a typed expiration date.
func (s *SubscriptionCreates) WithPlaceholders(placeholders *SubscriptionPlaceholders) *SubscriptionCreates {
	s.placeholders = placeholders
	return s
}

func NewSubscriptionCreates(store domain.SubscriptionCreateStore, tasks celery.Publisher, notifications *SubscriptionNotifier,
	now func() time.Time, logger *slog.Logger) *SubscriptionCreates {
	if logger == nil {
		logger = slog.Default()
	}
	return &SubscriptionCreates{store: store, tasks: tasks, notifications: notifications, now: now, logger: logger}
}

var errSubscriptionCreateValidation = &domain.SubscriptionCreateInvalid{Detail: map[string]string{"msg": "validation errors."}}

func objectSet(object domain.RequestObject, name string, value any) domain.RequestObject {
	for i, field := range object {
		if field.Name == name {
			object[i].Value = value
			return object
		}
	}
	return append(object, domain.RequestField{Name: name, Value: value})
}

func objectDelete(object domain.RequestObject, name string) domain.RequestObject {
	for i, field := range object {
		if field.Name == name {
			return append(object[:i:i], object[i+1:]...)
		}
	}
	return object
}

func objectHas(object domain.RequestObject, name string) bool {
	_, present := object.Lookup(name)
	return present
}

// subscriptionCreateGroup parses the X-Group-Id header: the canonical UUID when
// Python's uuid.UUID(hex=...) accepts it (dashes anywhere included), else the
// text as sent. A blank header is no header. subscriptionRenewGroup then decides
// whether the managers can filter with it or must run unfiltered.
func subscriptionCreateGroup(header string) (stored *string, sent string) {
	header = strings.TrimSpace(header)
	if header == "" {
		return nil, ""
	}
	if parsed, err := paymentInfoParseUUID(header); err == nil {
		canonical := parsed.String()
		return &canonical, header
	}
	return &header, header
}

// Add follows subscription_add: owners and collaborators, the public form
// (anonymous) and a logged-in athlete naming an association.
func (s *SubscriptionCreates) Add(ctx context.Context, identity domain.ResolvedIdentity, request SubscriptionCreateRequest) (*SubscriptionCreateResult, error) {
	group, _ := subscriptionCreateGroup(request.Group)
	// A malformed header leaves the manager queries unfiltered but still fails
	// the first create: the query group is nil, the flag marks the failure point.
	queryGroup, invalidGroup := subscriptionRenewGroup(group)
	request.GroupInvalid = invalidGroup
	// _prepare_subscription_request logs data.get('is_family_wizard'): only a dict passes.
	data, ok := request.Body.(domain.RequestObject)
	if !ok {
		return nil, domain.ErrSubscriptionCreateServer
	}
	if identity.ScopeUser == nil {
		// An anonymous request is attributed to the user data['sport_association'] names.
		account, err := s.namedUser(ctx, data)
		if err != nil {
			return nil, err
		}
		identity.ScopeUser = account
		request.Anonymous = true
		request.Audit.ActorID, request.Audit.ActorEmail, request.Audit.AdditionalData = nil, nil, nil
	} else {
		request.Audit = paymentCategoryAuditContext(identity, request.Audit)
	}
	user := identity.ScopeUser
	quick := request.Mode != nil && *request.Mode == "only_associate"
	request.AthleteRequest = user.Role != domain.RoleAssociation && objectHas(data, "sport_association")
	// _prepare_subscription_request then assigns data['sport_association'] for an
	// association account in quick mode. A form body is an immutable QueryDict
	// unless a multipart body carried a file part (DRF hands the view a mutable
	// copy): the assignment raises, the 500 page, before any key is read.
	if quick && user.Role == domain.RoleAssociation && request.Form && !data.HasUpload() {
		return nil, domain.ErrSubscriptionCreateServer
	}
	if quick {
		if user.Role != domain.RoleAssociation && objectHas(data, "sport_association") && objectHas(data, "associate") {
			return nil, s.quickMember(ctx, identity, data, queryGroup)
		}
		if user.Role == domain.RoleAssociation {
			tx, err := s.store.BeginSubscriptionCreate(ctx)
			if err != nil {
				return nil, err
			}
			tx.SetGroup(queryGroup)
			association, err := tx.ActiveAssociationOf(ctx, user.ID)
			_ = tx.Rollback()
			if err != nil {
				return nil, err
			}
			if association == nil {
				// .first().sport_association_id on None.
				return nil, domain.ErrSubscriptionCreateServer
			}
			// data['sport_association'] is set to it: an associate key takes the quick branch.
			if objectHas(data, "associate") {
				return s.quick(ctx, identity, data, request, queryGroup)
			}
		}
		return nil, errSubscriptionCreateValidation
	}
	for _, key := range []string{"new_user_account", "associate_data", "associate_tutor_data", "signature"} {
		if !objectHas(data, key) {
			return nil, errSubscriptionCreateValidation
		}
	}
	if family, _ := data.Lookup("is_family_wizard"); family == true {
		return s.family(ctx, identity, data, request, queryGroup)
	}
	return s.create(ctx, identity, data, request, queryGroup)
}

// subscriptionMedicalDate is create_subscription's expiration parse:
// strptime '%d/%m/%Y' when the text has a '/', else '%Y-%m-%d'. ok is false
// for every value the view's try blocks swallow (non-strings, bad dates);
// refused marks digits strptime accepts beyond ASCII.
func subscriptionMedicalDate(value any) (date time.Time, ok, refused bool) {
	text, isText := value.(string)
	if !isText {
		return time.Time{}, false, false
	}
	if dto.StrptimeUnicodeDigits(text) {
		return time.Time{}, false, true
	}
	if strings.Contains(text, "/") {
		iso, parsed := dto.PythonStrptimeDMY(text)
		if !parsed {
			return time.Time{}, false, false
		}
		date, err := time.Parse("2006-01-02", iso)
		return date, err == nil, false
	}
	date, parsed := dto.PythonStrptimeDate(text)
	return date, parsed, false
}

// subscriptionCreateToday is datetime.now().strftime('%Y-%m-%d') (naive, process clock).
func subscriptionCreateToday(now time.Time) string {
	return DjangoNaiveNow(now, time.Local).Format("2006-01-02")
}

// refuse rejects the inputs whose create_subscription paths are not ported,
// before anything is read or written. today is the naive current date.
func subscriptionCreateRefuse(data domain.RequestObject, today string) error {
	// A preregistration that names the subscription it renews is not ported inside a renewal or a draft approval.
	if value, _ := data.Lookup("preregistration"); value == true && subscriptionRenewalRequested(data) {
		return domain.ErrSubscriptionCreateNotPorted
	}
	return subscriptionCreateRefuseInputs(data, today)
}

// subscriptionCreateRefuseInputs is refuse for subscription/add, which ports
// the next-season preregistration.
func subscriptionCreateRefuseInputs(data domain.RequestObject, today string) error {
	if medical, ok := data.Lookup("medical_certificate"); ok {
		if object, isObject := medical.(domain.RequestObject); isObject {
			// Digits strptime accepts beyond ASCII are not ported.
			if expiration, present := object.Lookup("certificate_expring_date"); present {
				if _, _, refused := subscriptionMedicalDate(expiration); refused {
					return domain.ErrSubscriptionCreateNotPorted
				}
			}
		}
	}
	return nil
}

// subscriptionCustomKind is custom_data['type_of_associate'] as the icontains text of subscription_exists, nil when custom_data is not
// a dict holding a truthy value.
func subscriptionCustomKind(data domain.RequestObject) *string {
	custom, _ := data.Lookup("custom_data")
	object, ok := custom.(domain.RequestObject)
	if !ok {
		return nil
	}
	if kind, _ := object.Lookup("type_of_associate"); subscriptionTruthy(kind) {
		text := dto.PythonStr(kind)
		return &text
	}
	return nil
}

func subscriptionCreateIndex(data domain.RequestObject, name string) int {
	for i, field := range data {
		if field.Name == name {
			return i
		}
	}
	return -1
}

func dtoBoolean(value any) (bool, string) {
	switch v := value.(type) {
	case bool:
		return v, ""
	case json.Number:
		if v == "1" {
			return true, ""
		}
		if v == "0" {
			return false, ""
		}
	case string:
		switch v {
		case "t", "T", "y", "Y", "yes", "Yes", "YES", "true", "True", "TRUE", "on", "On", "ON", "1":
			return true, ""
		case "f", "F", "n", "N", "no", "No", "NO", "false", "False", "FALSE", "off", "Off", "OFF", "0":
			return false, ""
		}
	}
	return false, "invalid"
}

func subscriptionTruthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case json.Number:
		number, ok := new(big.Float).SetString(string(v))
		return !ok || number.Sign() != 0
	case domain.RequestObject:
		return len(v) > 0
	case []any:
		return len(v) > 0
	}
	return true
}

func subscriptionCreateDay(value time.Time) domain.CalendarDate {
	return domain.CalendarDate{Year: value.Year(), Month: int(value.Month()), Day: value.Day()}
}

func calendarTime(value domain.CalendarDate) time.Time {
	return time.Date(value.Year, time.Month(value.Month), value.Day, 0, 0, 0, 0, time.UTC)
}

// subscriptionAudit carries the request's audit context into the entries.
type subscriptionAudit struct {
	tx      domain.SubscriptionCreateTransaction
	context domain.LoginAuditContext
	now     func() time.Time
	// group is the canonical X-Group-Id; sent is the header text auditlog writes
	// for rows created through the manager (the instance keeps the sent spelling).
	group     *string
	sentGroup string
	// invalidGroup marks a malformed header: the manager queries ignore it and
	// the first row created through a manager raises (auditlog never runs).
	invalidGroup bool
	// inserted marks associates created through the manager with the group: a
	// later save compares the stored uuid with the sent spelling.
	inserted map[string]bool
}

func (a subscriptionAudit) invalid() bool {
	// A malformed header reaches flows that build their own audit literal (quick):
	// there the query group is nil while the sent text is not.
	return a.invalidGroup || (a.group == nil && a.sentGroup != "")
}

func (a subscriptionAudit) visible(stored *domain.SubscriptionEditAssociate) bool {
	if a.group == nil {
		return true
	}
	return stored != nil && stored.GroupID != nil && *stored.GroupID == *a.group
}

// index is the AuditLogIndex resolution of the entry: the group-aware manager
// finds the row (direct), else the live association in the changes, else the
// caller's fallback (the actor's association).
func (a subscriptionAudit) index(stored *domain.SubscriptionEditAssociate, association *string, path string) (*string, string) {
	if a.group == nil || a.visible(stored) {
		return association, path
	}
	if stored != nil && stored.AssociationID != nil {
		return stored.AssociationID, "changes_json.sport_association"
	}
	return association, path
}

func (a subscriptionAudit) associate(ctx context.Context, key string, action int, created bool, stored *domain.SubscriptionEditAssociate,
	changes map[string][2]string, association *string, path string) error {
	indexAssociation, indexPath := a.index(stored, association, path)
	if created {
		var overrides map[string][2]string
		if a.group != nil && a.inserted[key] {
			overrides = map[string][2]string{"group": {"None", a.sentGroup}}
		}
		return a.tx.Audit(ctx, domain.SubscriptionEditAudit{Model: "associate", Key: key, Action: action, Created: true, Changes: changes,
			Overrides: overrides, Association: indexAssociation, Path: indexPath, Context: a.context, At: a.now().UTC()})
	}
	if !a.visible(stored) {
		// auditlog's old row lookup goes through the group-aware manager and
		// finds nothing: it renders every field against None, like a create.
		return a.tx.Audit(ctx, domain.SubscriptionEditAudit{Model: "associate", Key: key, Action: action, Created: true, Changes: nil,
			Association: indexAssociation, Path: indexPath, Context: a.context, At: a.now().UTC()})
	}
	if a.inserted[key] && a.group != nil && a.sentGroup != *a.group {
		if changes == nil {
			changes = map[string][2]string{}
		}
		changes["group"] = [2]string{*a.group, a.sentGroup}
	}
	if len(changes) == 0 {
		return nil
	}
	return a.tx.Audit(ctx, domain.SubscriptionEditAudit{Model: "associate", Key: key, Action: action, Created: false, Changes: changes,
		Association: indexAssociation, Path: indexPath, Context: a.context, At: a.now().UTC()})
}

func (a subscriptionAudit) created(ctx context.Context, model, representation, table, column, key string, overrides map[string][2]string,
	association *string, path string) error {
	if a.invalid() {
		// Payment.objects.create / Subscription.objects.create through the manager
		// fails on the raw header; the transaction rolls back.
		return domain.ErrSubscriptionCreateServer
	}
	if a.group != nil && (model == "payment" || model == "subscription") {
		if overrides == nil {
			overrides = map[string][2]string{}
		}
		overrides["group"] = [2]string{"None", a.sentGroup}
	}
	return a.tx.AuditEntry(ctx, domain.SubscriptionCreateAudit{App: "application", Model: model, Representation: representation, Table: table,
		Column: column, Key: key, Action: 0, Created: true, Overrides: overrides, Association: association, Path: path, Context: a.context,
		At: a.now().UTC()})
}

func (a subscriptionAudit) updated(ctx context.Context, model, representation, key string, changes map[string][2]string, association *string,
	path string) error {
	if len(changes) == 0 {
		return nil
	}
	return a.tx.AuditEntry(ctx, domain.SubscriptionCreateAudit{App: "application", Model: model, Representation: representation, Key: key,
		Action: 1, Changes: changes, Association: association, Path: path, Context: a.context, At: a.now().UTC()})
}

// saved is the subscription create_subscription returns, with its after-commit work.
type subscriptionCreated struct {
	id, association, associateName string
	result                         SubscriptionCreateResult
	// The renewal's clean-up reads the saved associate and the dates.
	first, last, tax *string
	start, end       *domain.CalendarDate
	userID           string
	// validated is validate_broadcast's result when it already ran (approve).
	validated *bool
	// welcome is the new account's e-mail, athleteMail the owner's reminder for an athlete user.
	welcome, athleteMail *celery.Task
	// notifyUser receives the notification (create_subscription's user); empty: request.user.
	notifyUser string
}

func (s *SubscriptionCreates) create(ctx context.Context, identity domain.ResolvedIdentity, data domain.RequestObject,
	request SubscriptionCreateRequest, group *string) (*SubscriptionCreateResult, error) {
	// A preregistration that renews a subscription replaces these inputs: they are checked after the fill.
	if !subscriptionRenewalRequested(data) {
		if err := subscriptionCreateRefuseInputs(data, subscriptionCreateToday(s.now())); err != nil {
			return nil, err
		}
	}
	tx, err := s.store.BeginSubscriptionCreate(ctx)
	if err != nil {
		return nil, err
	}
	tx.SetGroup(group)
	defer tx.Rollback()
	keys := []string{}
	request.StorageKeys = &keys
	request.GroupID = group
	created, err := s.createSubscription(ctx, tx, identity, data, request)
	if err == nil {
		err = tx.Commit()
	}
	if err != nil {
		s.cleanupStorage(ctx, keys)
		return nil, err
	}
	s.afterCommit(ctx, identity, created, request)
	return &created.result, nil
}

func (s *SubscriptionCreates) createSubscription(ctx context.Context, tx domain.SubscriptionCreateTransaction, identity domain.ResolvedIdentity,
	data domain.RequestObject, request SubscriptionCreateRequest) (*subscriptionCreated, error) {
	server := domain.ErrSubscriptionCreateServer
	group := request.GroupID
	user := identity.ScopeUser
	now := s.now().UTC()
	today := subscriptionCreateDay(now)
	// data['preregistration'] is True: the next season. With a subscription_id
	// it renews that subscription (fill_data_preregisration): the registration
	// is the renewed member's, owned by the renewed subscription's account.
	preregistration, renewal := false, false
	var renewedUser *string
	if flag, _ := data.Lookup("preregistration"); flag == true {
		if subscriptionRenewalRequested(data) {
			filled, owner, err := s.fillPreregistration(ctx, tx, identity, data, request)
			if err != nil {
				return nil, err
			}
			// The inputs refused for a registration, now on the renewed subscription's data.
			if err = subscriptionCreateRefuseInputs(filled, subscriptionCreateToday(s.now())); err != nil {
				return nil, err
			}
			data, renewedUser, renewal = filled, owner, true
		}
		preregistration = true
	}
	associateValue, present := data.Lookup("associate_data")
	if !present {
		return nil, pythonKeyError("associate_data")
	}
	if upload, isUpload := associateValue.(domain.RequestUpload); isUpload {
		// data['associate_data']['born_date'] on a multipart file part: the TypeError
		// names Django's upload class. Nothing has been written yet.
		return nil, renewUploadSubscriptError(upload)
	}
	associate, ok := associateValue.(domain.RequestObject)
	if !ok {
		return nil, pythonSubscriptError(associateValue)
	}
	bornValue, present := associate.Lookup("born_date")
	if !present {
		return nil, pythonKeyError("born_date")
	}
	// person_date(str(born_date)): Python str() of any JSON value (None, a
	// boolean, an integer, a float or a container), then dateutil's reading.
	bornText := dto.PythonStr(bornValue)
	born, result, failure := dto.PersonDateText(bornText, now)
	if result == dto.DateutilUnsupported {
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	if result != dto.DateutilParsed {
		// str() of dateutil's exception.
		return nil, &domain.SubscriptionCreateFailure{Text: failure}
	}
	age := relativeYears(calendarTime(today), born)
	tutorValue, _ := data.Lookup("associate_tutor_data")
	var tutor domain.RequestObject
	if tutorValue != nil {
		if tutor, ok = tutorValue.(domain.RequestObject); !ok {
			return nil, server
		}
		if tutor, associate, err := subscriptionCreateNormalize(tutor, associate, age); err != nil {
			return nil, err
		} else {
			data = objectSet(objectSet(data, "associate_tutor_data", tutor), "associate_data", associate)
		}
		tutorValue, _ = data.Lookup("associate_tutor_data")
		tutor = tutorValue.(domain.RequestObject)
		associateValue, _ = data.Lookup("associate_data")
		associate = associateValue.(domain.RequestObject)
	}
	// The serializers read new_user_account and signature first.
	for _, key := range []string{"new_user_account", "signature"} {
		if !objectHas(data, key) {
			return nil, pythonKeyError(key)
		}
	}
	// data['medical_certificate']['medical_id'], read before the association;
	// a None certificate fails later, in the elif.
	var medicalID any
	medicalNull := false
	if medical, present := data.Lookup("medical_certificate"); present {
		if medical != nil {
			object, isObject := medical.(domain.RequestObject)
			if !isObject {
				return nil, pythonAttributeError(medical, "keys")
			}
			medicalID, _ = object.Lookup("medical_id")
		} else {
			medicalNull = true
		}
	}
	var settings *domain.SubscriptionCreateSettings
	var count int
	var err error
	// member is create_subscription's user: request.user, until a new member account replaces it.
	var member domain.SubscriptionCreateMember
	if request.AthleteRequest {
		// The association of User.objects.get(username=data['sport_association']);
		// accounts other than athletes are not ported.
		if user.Role != domain.RoleAthlete {
			return nil, domain.ErrSubscriptionCreateNotPorted
		}
		named, _ := data.Lookup("sport_association")
		owner, err := subscriptionNamedMember(ctx, tx, named)
		if err != nil {
			return nil, err
		}
		if settings, count, err = tx.Settings(ctx, owner.ID); err != nil {
			return nil, err
		}
		if count != 1 {
			return nil, server // SportAssociation.objects.get(user=...)
		}
		own, err := tx.Member(ctx, user.ID)
		if err != nil {
			return nil, err
		}
		if own == nil {
			return nil, server
		}
		member = *own
	} else {
		if user.Role == domain.RoleAthlete {
			return nil, &domain.PermissionDenied{Detail: "User is not a sport association"}
		}
		if settings, count, err = tx.Settings(ctx, user.ID); err != nil {
			return nil, err
		}
		if count != 1 {
			return nil, subscriptionAssociationGet(count)
		}
		if named, present := data.Lookup("sport_association"); present {
			// User.objects.get(username=...) and its SportAssociation; a miss keeps request.user's.
			if err := s.namedAssociation(ctx, tx, named, settings.AssociationID); err != nil {
				return nil, err
			}
		}
		member = domain.SubscriptionCreateMember{ID: user.ID, Role: user.Role, StartDay: settings.StartDay, StartMonth: settings.StartMonth,
			CustomEnd: settings.CustomEnd, EndDay: settings.EndDay, EndMonth: settings.EndMonth}
	}
	association := settings.AssociationID
	associate = objectSet(associate, "sport_association", association)
	associate = objectSet(associate, "user", settings.OwnerID)
	data = objectSet(data, "associate_data", associate)
	if tutor != nil {
		tutor = objectSet(tutor, "sport_association", association)
		tutor = objectSet(tutor, "user", settings.OwnerID)
		data = objectSet(data, "associate_tutor_data", tutor)
	}
	rule := domain.CalendarRule{StartDay: settings.StartDay, StartMonth: settings.StartMonth, CustomEnd: settings.CustomEnd,
		EndDay: settings.EndDay, EndMonth: settings.EndMonth}
	// date_from/date_to: the association's season start with the custom end of create_subscription's user.
	seasonRule := domain.CalendarRule{StartDay: settings.StartDay, StartMonth: settings.StartMonth, CustomEnd: member.CustomEnd,
		EndDay: member.EndDay, EndMonth: member.EndMonth}
	season, err := SeasonRange(ctx, now, seasonRule)
	if err == nil && preregistration {
		season, err = SeasonRange(ctx, season.End.AddDate(0, 0, 1), seasonRule)
	}
	if err != nil {
		return nil, subscriptionCreateCalendar(err)
	}
	// check_if_subscription_exists on the raw names and tax code.
	names := make([]*string, 3)
	for i, name := range []string{"first_name", "last_name", "tax_code"} {
		value, _ := associate.Lookup(name)
		text, supported := dto.SubscriptionCreateText(value)
		if !supported {
			return nil, domain.ErrSubscriptionCreateNotPorted
		}
		names[i] = text
	}
	if custom, present := data.Lookup("custom_data"); present && custom != nil {
		if _, isObject := custom.(domain.RequestObject); !isObject {
			// data.get('custom_data', {}).get(...) on a non-dict.
			return nil, pythonAttributeError(custom, "get")
		}
	}
	// Q(custom_data__icontains=custom_data['type_of_associate']) with str(value).
	customKind := subscriptionCustomKind(data)
	existing, err := tx.Existing(ctx, association, names[0], names[1], names[2], subscriptionCreateDay(season.Start), subscriptionCreateDay(season.End), customKind)
	if err != nil {
		return nil, err
	}
	if existing != nil {
		// not is_renewal_available(): is_current, then has_future_renewal.
		duplicate := existing.Start == nil || existing.End == nil ||
			(!calendarTime(today).Before(existing.Start.UTC()) && !calendarTime(today).After(existing.End.UTC()))
		if !duplicate && existing.AssociateID != nil {
			if duplicate, err = tx.FutureRenewal(ctx, association, subscriptionCreateDay(existing.End.UTC()), existing.TaxCode); err != nil {
				return nil, err
			}
		}
		if duplicate {
			return nil, domain.ErrSubscriptionCreateDuplicate
		}
	}
	audit := subscriptionAudit{tx: tx, context: request.Audit, now: s.now, group: group, sentGroup: request.Group,
		invalidGroup: request.GroupInvalid, inserted: map[string]bool{}}
	ownAssociation := &association
	// A new member account (or the user with its e-mail) replaces user from here on.
	newMember, err := s.newMember(ctx, tx, data, settings, request)
	if err != nil {
		return nil, err
	}
	var welcome *celery.Task
	if newMember != nil {
		member, welcome = newMember.member, newMember.welcome
	}
	memberID := member.ID
	saved, err := s.saveAssociate(ctx, tx, audit, associate, association, nil, group)
	if err != nil {
		return nil, err
	}
	var savedTutor *domain.SubscriptionEditAssociate
	if saved.IsMinor && tutor != nil {
		savedTutor, err = s.saveAssociate(ctx, tx, audit, tutor, association, &saved.ID, group)
		if err != nil {
			return nil, err
		}
		if savedTutor != nil {
			old := *savedTutor
			savedTutor.UserID = &memberID
			if err = tx.SaveAssociate(ctx, *savedTutor); err != nil {
				return nil, err
			}
			if err = audit.associate(ctx, savedTutor.ID, 1, false, &old, associateChanges(old, *savedTutor), ownAssociation, "direct"); err != nil {
				return nil, err
			}
			relations, err := tx.Relations(ctx, saved.ID)
			if err != nil {
				return nil, err
			}
			linked := false
			for _, relation := range relations {
				linked = linked || relation.TutorID == savedTutor.ID
			}
			switch {
			case linked:
				// associate_tutor in associate.tutors.all(): nothing to add.
			case len(relations) > 0:
				// get_main_tutor(), then tutors.add(primary_tutor) adds nothing: the new tutor is not linked.
				if _, err = s.mainTutor(ctx, tx, saved.ID); err != nil {
					return nil, err
				}
			default:
				relation, err := tx.InsertRelation(ctx, saved.ID, savedTutor.ID)
				if err != nil {
					return nil, err
				}
				key := fmt.Sprint(relation)
				if err = tx.AuditEntry(ctx, domain.SubscriptionCreateAudit{App: "application", Model: "associatetutorrelation",
					Representation: "AssociateTutorRelation", Table: "application_associatetutorrelation", Column: "id", Key: key, Created: true,
					Association: ownAssociation, Path: "associate.sport_association", Context: request.Audit, At: s.now().UTC()}); err != nil {
					return nil, err
				}
			}
		}
	}
	// associate.save() of the instance the serializer returned. When the tutor save updated
	// that same row (the tutor's tax code matched the minor's), the stored row now holds the
	// tutor's fields: the stale instance writes the minor's values back, and auditlog's entry
	// compares against that stored row.
	old := *saved
	if savedTutor != nil && savedTutor.ID == saved.ID {
		old = *savedTutor
	}
	saved.UserID = &memberID
	if err = tx.SaveAssociate(ctx, *saved); err != nil {
		return nil, err
	}
	if err = audit.associate(ctx, saved.ID, 1, false, &old, associateChanges(old, *saved), ownAssociation, "direct"); err != nil {
		return nil, err
	}
	signatureValue, _ := data.Lookup("signature")
	signature, invalid := dto.ParseSignatureRequest(signatureValue)
	if invalid != nil {
		return nil, &domain.SubscriptionCreateInvalid{Fields: invalid}
	}
	if signature.Signed {
		// Signature.objects.create(signature=signature.data, user=user).
		if err = tx.InsertSignature(ctx, uuid.NewString(), memberID, *signature.Data, request.Audit,
			s.now().UTC().Truncate(time.Microsecond)); err != nil {
			return nil, err
		}
	}
	if medicalNull {
		// The elif's data['medical_certificate'].keys() on None.
		return nil, pythonAttributeError(nil, "keys")
	}
	var medical *string
	if medicalID != nil {
		text, isText := medicalID.(string)
		if !isText {
			return nil, domain.ErrSubscriptionCreateNotPorted
		}
		parsed, err := paymentInfoParseUUID(text)
		if err != nil {
			return nil, djangoInvalidUUID(text)
		}
		id := parsed.String()
		// The association account's and the requester's certificates, those of the
		// association's subscriptions and uploads without an account; any other id is
		// treated like an unknown one.
		accounts := []string{settings.OwnerID}
		if requester := request.RequesterID; requester != "" {
			accounts = append(accounts, requester)
		} else if !request.Anonymous {
			accounts = append(accounts, identity.ScopeUser.ID)
		}
		found, owner, err := tx.LinkableCertificate(ctx, id, association, accounts)
		if err != nil {
			return nil, err
		}
		if found {
			medical = &id
			changes := map[string][2]string{}
			user := owner
			if owner == nil {
				user = &settings.OwnerID
				changes["user"] = [2]string{"None", settings.OwnerID}
			}
			// The expiration date: a parsed datetime assigned to the DateField,
			// so auditlog compares str(date) with str(datetime).
			var expiration *time.Time
			if object, isObject := data.Lookup("medical_certificate"); isObject {
				if value, present := object.(domain.RequestObject).Lookup("certificate_expring_date"); present {
					if date, parsed, _ := subscriptionMedicalDate(value); parsed {
						expiration = &date
						stored, err := tx.CertificateExpiration(ctx, id)
						if err != nil {
							return nil, err
						}
						before := "None"
						if stored != nil {
							before = stored.Format("2006-01-02")
						}
						changes["expiration_date"] = [2]string{before, subscriptionPythonDateTime(date)}
					}
				}
			}
			if len(changes) > 0 {
				if err = tx.SaveCertificate(ctx, id, *user, expiration); err != nil {
					return nil, err
				}
				actorAssociation, err := s.actorAssociation(ctx, tx, request.Audit)
				if err != nil {
					return nil, err
				}
				if err = audit.updated(ctx, "medicalcertificate", "MedicalCertificate", id, changes,
					actorAssociation, "actor.sport_association"); err != nil {
					return nil, err
				}
			}
		}
	}
	if medicalID == nil {
		if medical, err = s.createPlaceholder(ctx, tx, audit, data, associate, settings.OwnerID, request); err != nil {
			return nil, err
		}
	}
	subscriptionType := int64(2)
	if value, present := associate.Lookup("type"); present && value != nil {
		if value != "" {
			parsed, ok := dto.SubscriptionCreateInt(value)
			if !ok {
				return nil, pythonIntError(value)
			}
			if !parsed.IsInt64() {
				return nil, server
			}
			subscriptionType = parsed.Int64()
		}
	}
	fees, err := s.fees(ctx, tx, data, settings, subscriptionType)
	if err != nil {
		return nil, err
	}
	var paymentID *string
	amountJSON := "0"
	if fees.payment {
		// The amount filter and the insert both pass through the DecimalField, which
		// raises for an infinite or NaN value.
		amount, ok := dto.SubscriptionCreateDecimalValue(fees.amount)
		if !ok {
			return nil, server
		}
		day := calendarTime(today)
		reusable, tied, err := tx.ReusablePayments(ctx, saved.ID, association, amount, day)
		if err != nil {
			return nil, err
		}
		if tied {
			// order_by('-creation_date').first(): the database breaks the tie, as in Django.
			newest, err := tx.NewestReusablePayment(ctx, saved.ID, association, amount, day)
			if err != nil {
				return nil, err
			}
			if newest == nil {
				return nil, server
			}
			reusable = []string{*newest}
		}
		if len(reusable) > 0 {
			paymentID = &reusable[0]
			stored, err := tx.PaymentAmount(ctx, reusable[0])
			if err != nil {
				return nil, err
			}
			number, _, _ := dto.SubscriptionCreateFloat(json.Number(stored))
			amountJSON = dto.PythonFloatText(number)
		} else {
			category := fees.category
			if category == nil {
				// Payment.save() looks the default category up again.
				if category, err = tx.Category(ctx, nil, association); err != nil {
					return nil, err
				}
			}
			account, err := tx.DefaultBankAccount(ctx, association)
			if err != nil {
				return nil, err
			}
			id := uuid.NewString()
			row := domain.SubscriptionCreatePayment{ID: id, AssociationID: association, AssociateID: saved.ID, UserID: memberID, Amount: amount,
				CategoryID: category, AccountID: account, MetaCategories: fees.metaCategories, Meta: fees.meta, PaymentDate: day, Created: s.now().UTC()}
			if request.GroupInvalid {
				// Payment.objects.create through the manager fails on the raw header.
				return nil, server
			}
			if err = tx.InsertPayment(ctx, row); err != nil {
				return nil, err
			}
			if err = audit.created(ctx, "payment", "Payment", "application_payment", "payment_id", id,
				map[string][2]string{"amount": {"None", dto.PythonFloatText(fees.amount)}}, ownAssociation, "direct"); err != nil {
				return nil, err
			}
			paymentID = &id
			amountJSON = dto.PythonFloatText(fees.amount)
		}
	}
	latest, err := tx.LatestNumber(ctx, association)
	if err != nil {
		return nil, err
	}
	number := big.NewInt(settings.MembershipStartingNumber)
	if latest != nil {
		stored, ok := new(big.Int).SetString(strings.TrimSpace(*latest), 10)
		if !ok {
			return nil, server
		}
		if stored.Cmp(number) > 0 {
			number = stored
		}
	}
	number = new(big.Int).Add(number, big.NewInt(1))
	role := int64(1)
	if value, present := associate.Lookup("role"); present && value != nil {
		if value != "" {
			parsed, ok := dto.SubscriptionCreateInt(value)
			if !ok {
				return nil, pythonIntError(value)
			}
			if !parsed.IsInt64() {
				return nil, server
			}
			role = parsed.Int64()
		}
	}
	if value, present := associate.Lookup("subscription_number"); present && value != nil {
		parsed, ok := dto.SubscriptionCreateInt(value)
		if !ok {
			return nil, pythonIntError(value)
		}
		number = parsed
	}
	var customData, additionalFields *string
	if value, present := data.Lookup("custom_data"); present && value != nil {
		text := string(dto.PythonJSONDumps(value))
		customData = &text
	}
	if value, present := data.Lookup("additional_fields"); present && value != nil {
		text := string(dto.PythonJSONDumps(value))
		additionalFields = &text
	}
	planID, _ := data.Lookup("plan_id")
	membershipPlanID, _ := data.Lookup("membership_plan_id")
	var planName, membershipPlanName any
	if planID != nil {
		plan, err := subscriptionCreatePlan(settings.SubscriptionPlans, planID)
		if err != nil {
			return nil, err
		}
		if plan != nil {
			if planName, ok = plan.Lookup("name"); !ok {
				return nil, server
			}
		}
	}
	if membershipPlanID != nil {
		plan, err := subscriptionCreatePlan(settings.MembershipPlans, membershipPlanID)
		if err != nil {
			return nil, err
		}
		if plan != nil {
			if membershipPlanName, ok = plan.Lookup("name"); !ok {
				return nil, server
			}
		}
	}
	meta := string(dto.PythonJSONDumps(domain.RequestObject{{Name: "plan_id", Value: planID}, {Name: "plan_name", Value: planName},
		{Name: "membership_plan_id", Value: membershipPlanID}, {Name: "membership_plan_name", Value: membershipPlanName}}))
	// current_date_from/to of create_subscription's user (a new member's own settings).
	memberRule := domain.CalendarRule{StartDay: member.StartDay, StartMonth: member.StartMonth, CustomEnd: member.CustomEnd,
		EndDay: member.EndDay, EndMonth: member.EndMonth}
	memberSeason, err := SeasonRange(ctx, now, memberRule)
	if err == nil && preregistration {
		memberSeason, err = SeasonRange(ctx, memberSeason.End.AddDate(0, 0, 1), memberRule)
	}
	if err != nil {
		return nil, subscriptionCreateCalendar(err)
	}
	// Subscription.objects.create: subscription_exists on the saved associate
	// for yesterday..tomorrow (a preregistration: its own dates), then Subscription.save()'s dates.
	from, to := subscriptionCreateDay(calendarTime(today).AddDate(0, 0, -1)), subscriptionCreateDay(calendarTime(today).AddDate(0, 0, 1))
	createdAt := s.now().UTC()
	given := domain.SubscriptionDates{}
	if preregistration {
		// creation_date is date_from; start_date and end_date the user's next season.
		from, to = subscriptionCreateDay(memberSeason.Start), subscriptionCreateDay(memberSeason.End)
		createdAt = season.Start
		given = domain.SubscriptionDates{Start: &from, End: &to}
	}
	// The manager's create() runs subscription_exists with the new subscription's custom_data too.
	duplicate, err := tx.Existing(ctx, association, saved.FirstName, saved.LastName, saved.TaxCode, from, to, subscriptionCustomKind(data))
	if err != nil {
		return nil, err
	}
	if duplicate != nil {
		return nil, domain.ErrSubscriptionCreateDuplicate
	}
	dates, err := ResolveSubscriptionDates(ctx, createdAt, domain.SubscriptionDateRule{Season: rule, Duration: settings.SubscriptionDuration},
		given)
	if err != nil {
		return nil, subscriptionCreateCalendar(err)
	}
	subscriptionID := uuid.NewString()
	status := int64(1)
	if signature.Signed {
		status = 2 // PENDING
	}
	row := domain.SubscriptionCreateRow{ID: subscriptionID, AssociationID: association, AssociateID: saved.ID, UserID: memberID,
		Type: subscriptionType, Role: role, Status: status, MedicalID: medical, PaymentID: paymentID, CustomData: customData,
		AdditionalFields: additionalFields, Meta: meta, Number: number.String(), SubscriptionType: settings.DefaultMembershipType,
		Created: createdAt, Start: dates.Start, End: dates.End, Renewal: renewal, RenewedUser: renewedUser}
	if request.GroupInvalid {
		// Subscription.objects.create through the manager fails on the raw header.
		return nil, server
	}
	if err = tx.InsertSubscription(ctx, row); err != nil {
		return nil, err
	}
	var subscriptionChanges map[string][2]string
	if preregistration {
		// The create entry renders the aware datetimes assigned to the date fields.
		aware := func(day time.Time) [2]string {
			return [2]string{"None", fmt.Sprintf("%04d-%02d-%02d 00:00:00+00:00", day.Year(), int(day.Month()), day.Day())}
		}
		subscriptionChanges = map[string][2]string{"start_date": aware(memberSeason.Start), "end_date": aware(memberSeason.End)}
	}
	if err = audit.created(ctx, "subscription", "Subscription", "application_subscription", "subscription_id", subscriptionID, subscriptionChanges,
		ownAssociation, "direct"); err != nil {
		return nil, err
	}
	if signature.Signed && signature.Data != nil && *signature.Data != "" {
		url, key, err := s.uploadSignature(ctx, subscriptionID, *signature.Data, request)
		if err != nil {
			return nil, err
		}
		if err = tx.SaveSubscriptionSignature(ctx, subscriptionID, association, url, key, request.Audit, s.now().UTC()); err != nil {
			return nil, err
		}
	}
	if subscriptionType == 2 || subscriptionType == 3 {
		membershipType := ""
		if settings.DefaultMembershipType != nil && *settings.DefaultMembershipType != "" {
			membershipType = *settings.DefaultMembershipType
		} else if membershipPlanName != nil {
			// A CharField stores str() of any other value the plan's name holds.
			membershipType = dto.PythonStr(membershipPlanName)
		} else {
			membershipType = fmt.Sprintf("Tesseramento %d/%d", memberSeason.Start.Year(), memberSeason.End.Year())
		}
		var start, end *domain.CalendarDate
		from, to := subscriptionCreateDay(memberSeason.Start), subscriptionCreateDay(memberSeason.End)
		switch settings.MembershipDuration {
		case 1:
			last := subscriptionCreateDay(calendarTime(from).AddDate(1, 0, 0).AddDate(0, 0, -1))
			start, end = &from, &last
		case 2:
			start, end = &from, &to
		case 3:
			created := subscriptionCreateDay(createdAt)
			start, end = &created, &to
		}
		var price *string
		rendered := ""
		if fees.membershipSet {
			if fees.membershipFloat {
				// DecimalField(max_digits=9, decimal_places=2): PostgreSQL rounds the
				// decimal Django sends to the stored two places; a later save() reads
				// that stored value back against the in-memory float.
				text, ok := dto.SubscriptionCreateDecimalStored(fees.membershipFee)
				if !ok {
					return nil, server
				}
				price, rendered = &text, dto.PythonFloatText(fees.membershipFee)
			} else {
				zero := "0.00"
				price, rendered = &zero, "0"
			}
		}
		membershipID := uuid.NewString()
		membership := domain.SubscriptionCreateMembership{ID: membershipID, SubscriptionID: subscriptionID, AssociationID: association,
			AssociateID: saved.ID, Number: number.String(), Type: membershipType, Price: price, PaymentID: paymentID, Created: s.now().UTC(),
			Start: start, End: end}
		if err = tx.InsertMembership(ctx, membership); err != nil {
			return nil, err
		}
		var overrides map[string][2]string
		if price != nil {
			overrides = map[string][2]string{"price": {"None", rendered}}
		}
		if err = audit.created(ctx, "subscriptionmembership", "SubscriptionMembership", "application_subscriptionmembership",
			"subscription_membership_id", membershipID, overrides, ownAssociation, "direct"); err != nil {
			return nil, err
		}
		// subscription_membership.save(): the stored price against the in-memory number.
		if price != nil {
			if err = audit.updated(ctx, "subscriptionmembership", "SubscriptionMembership", membershipID,
				map[string][2]string{"price": {*price, rendered}}, ownAssociation, "direct"); err != nil {
				return nil, err
			}
		}
	}
	name := strings.TrimSpace(subscriptionCreateValue(saved.FirstName) + " " + subscriptionCreateValue(saved.LastName))
	created := &subscriptionCreated{id: subscriptionID, association: association, associateName: name,
		result: SubscriptionCreateResult{PaymentID: paymentID, Amount: amountJSON}, first: saved.FirstName, last: saved.LastName,
		tax: saved.TaxCode, start: dates.Start, end: dates.End, userID: memberID, welcome: welcome, notifyUser: memberID}
	if member.Role == domain.RoleAthlete {
		created.athleteMail = s.athleteMail(settings, saved.FirstName, saved.LastName)
	}
	return created, nil
}

// subscriptionCreateValue is '%s' of a CharField value.
func subscriptionCreateValue(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

func subscriptionCreateCalendar(err error) error {
	if errors.Is(err, domain.ErrInvalidCalendar) {
		return domain.ErrSubscriptionCreateServer
	}
	return err
}

func (s *SubscriptionCreates) actorAssociation(ctx context.Context, tx domain.SubscriptionCreateTransaction, audit domain.LoginAuditContext) (*string, error) {
	if audit.ActorID == nil {
		return nil, nil
	}
	return tx.ActiveAssociationOf(ctx, *audit.ActorID)
}

// subscriptionCreateNormalize is create_subscription's tutor block: address
// copies, e-mail clean-up and sex upper-casing.
func subscriptionCreateNormalize(tutor, associate domain.RequestObject, age int) (domain.RequestObject, domain.RequestObject, error) {
	for _, name := range []string{"address_city", "address_cap"} {
		inAssociate, inTutor := objectHas(associate, name), objectHas(tutor, name)
		if !inAssociate && inTutor {
			value, _ := tutor.Lookup(name)
			associate = objectSet(associate, name, value)
		} else if age < 18 && inAssociate && !inTutor {
			value, _ := associate.Lookup(name)
			tutor = objectSet(tutor, name, value)
		}
	}
	if value, present := tutor.Lookup("address_cap"); present && value == "" {
		tutor = objectSet(tutor, "address_cap", nil)
	}
	clean := func(object domain.RequestObject) (domain.RequestObject, error) {
		value, present := object.Lookup("email")
		if !present || value == nil {
			return object, nil
		}
		text, isText := value.(string)
		if !isText {
			return nil, pythonAttributeError(value, "strip")
		}
		text = strings.ReplaceAll(strings.ReplaceAll(dto.PythonStrip(text), " ", ""), "..", ".")
		object = objectSet(object, "email", text)
		if !dto.SupplierEmail(text) {
			object = objectDelete(object, "email")
		}
		return object, nil
	}
	var err error
	if associate, err = clean(associate); err != nil {
		return nil, nil, err
	}
	if tutor, err = clean(tutor); err != nil {
		return nil, nil, err
	}
	upper := func(object domain.RequestObject) (domain.RequestObject, error) {
		value, present := object.Lookup("sex")
		if !present {
			return nil, pythonKeyError("sex")
		}
		if value == nil {
			return object, nil
		}
		text, isText := value.(string)
		if !isText {
			return nil, pythonAttributeError(value, "upper")
		}
		return objectSet(object, "sex", dto.PythonUpper(text)), nil
	}
	if associate, err = upper(associate); err != nil {
		return nil, nil, err
	}
	if tutor, err = upper(tutor); err != nil {
		return nil, nil, err
	}
	return tutor, associate, nil
}

// saveAssociate is AssociateSerializer(data=object): is_valid (raising for the
// associate, skipped for an invalid tutor: nil) and create().
func (s *SubscriptionCreates) saveAssociate(ctx context.Context, tx domain.SubscriptionCreateTransaction, audit subscriptionAudit,
	object domain.RequestObject, association string, minorOf *string, group *string) (*domain.SubscriptionEditAssociate, error) {
	isTutor := minorOf != nil
	bornValue, bornPresent := object.Lookup("born_date")
	var born time.Time
	var bornError []string
	// bornNull is born_date None: the serializer's DateField is not required (blank=True)
	// and accepts null (null=True), so a missing key is skipped and an explicit null is
	// validated as None; both store None. An associate without one never reaches this call
	// (create_subscription's first step fails), so that branch keeps refusing.
	bornNull := false
	switch v := bornValue.(type) {
	case nil:
		if !isTutor {
			return nil, domain.ErrSubscriptionCreateNotPorted
		}
		bornNull = true
	case string:
		parsed, ok := dto.SubscriptionCreateBornDate(v)
		if !ok {
			bornError = []string{dto.SubscriptionCreateDateFormat}
		}
		born = parsed
	default:
		// DateField: strptime of any other value (a number, a boolean, a list, a
		// dict) raises TypeError, the same 'invalid' error.
		bornError = []string{dto.SubscriptionCreateDateFormat}
	}
	write, invalid, err := dto.ParseAssociateTutor(object, born, func(id string) (bool, error) {
		if id != association {
			// The association is set from request.user; another one is not ported.
			return false, domain.ErrSubscriptionCreateNotPorted
		}
		return tx.AssociationExists(ctx, id)
	}, func(id string) (bool, error) { return tx.FamilyExists(ctx, id) })
	if err != nil {
		return nil, err
	}
	if invalid != nil || bornError != nil {
		if isTutor {
			return nil, nil
		}
		fields := map[string][]string{}
		if invalid != nil {
			for _, field := range invalid.Fields {
				fields[field.Name] = field.Codes
			}
		}
		if bornError != nil {
			fields["born_date"] = bornError
		}
		return nil, &domain.SubscriptionCreateInvalid{Fields: fields}
	}
	// apply is the serializer's setattr loop: a None born_date is stored NULL, except for a
	// missing key on an existing row (not in validated_data, so the stored value stays).
	apply := func(row *domain.SubscriptionEditAssociate) {
		stored := row.BornDate
		applyAssociateTutorWrite(row, write)
		if bornNull {
			row.BornDate = nil
			if !bornPresent {
				row.BornDate = stored
			}
		}
	}
	if write.AssociateID != nil && *write.AssociateID != "" {
		if audit.invalid() {
			// AssociateSerializer.create runs Associate.objects.create for an unknown
			// key (the manager fails on the raw header); an existing key is only saved.
			// The valid-header path keeps its exact queries.
			if parsed, err := paymentInfoParseUUID(*write.AssociateID); err == nil {
				existing, err := tx.AssociateAny(ctx, parsed.String())
				if err != nil {
					return nil, err
				}
				if existing == nil {
					return nil, domain.ErrSubscriptionCreateServer
				}
			}
		}
		return s.saveAssociateByID(ctx, tx, audit, write, association, minorOf, group)
	}
	ownAssociation := &association
	tax := write.Text["tax_code"]
	if tax != nil && *tax != "" {
		upper := dto.AssociateUpper(*tax)
		write.Text["tax_code"] = &upper
		matches, err := tx.TaxCodeMatches(ctx, upper, ownAssociation, group)
		if err != nil {
			return nil, err
		}
		if len(matches) > 0 {
			row := matches[0]
			if len(matches) > 1 {
				// MultipleObjectsReturned: order_by('-creation_date').first(); the database breaks a tie as in Django.
				newest, err := tx.NewestTaxCodeMatch(ctx, upper, ownAssociation, group)
				if err != nil {
					return nil, err
				}
				if newest == nil {
					return nil, domain.ErrSubscriptionCreateServer
				}
				row = *newest
			}
			// A tutor whose tax code matches the minor's own row reaches this update too:
			// get_or_create returns that row and the serializer's setattr loop writes the
			// tutor's fields over it; the view then links the row to itself and its stale
			// minor instance writes the old values back.
			old := row
			apply(&row)
			if err = tx.SaveAssociate(ctx, row); err != nil {
				return nil, err
			}
			if err = audit.associate(ctx, row.ID, 1, false, &old, associateChanges(old, row), ownAssociation, "direct"); err != nil {
				return nil, err
			}
			return &row, nil
		}
	}
	row := domain.SubscriptionEditAssociate{ID: uuid.NewString(), Sex: "A", CreationDate: s.now().UTC()}
	// Associate.objects.create (no tax code) runs the manager's create, which adds
	// the group; get_or_create (a tax code) does not, and the stored row keeps it null.
	if tax == nil || *tax == "" {
		if audit.invalid() {
			// The manager's create receives the raw header and fails before the insert.
			return nil, domain.ErrSubscriptionCreateServer
		}
		row.GroupID = group
	}
	apply(&row)
	if err = tx.InsertAssociate(ctx, row); err != nil {
		return nil, err
	}
	if row.GroupID != nil {
		audit.inserted[row.ID] = true
	}
	if err = audit.associate(ctx, row.ID, 0, true, &row, nil, ownAssociation, "direct"); err != nil {
		return nil, err
	}
	return &row, nil
}

// subscriptionCreatePlan is extract_the_*_plan: the first plan whose
// str(plan['id']) equals str(id); a plan without 'id' raises.
func subscriptionCreatePlan(raw []byte, id any) (domain.RequestObject, error) {
	parsed, err := dto.ParsePythonJSON(string(raw))
	if err != nil {
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	plans, ok := parsed.([]any)
	if !ok {
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	wanted := dto.PythonStr(id)
	for _, item := range plans {
		plan, ok := item.(domain.RequestObject)
		if !ok {
			return nil, domain.ErrSubscriptionCreateServer
		}
		value, present := plan.Lookup("id")
		if !present {
			return nil, domain.ErrSubscriptionCreateServer
		}
		// str() renders a JSON float id the way Python does ('1.0', '1e+16').
		if dto.PythonStr(value) == wanted {
			return plan, nil
		}
	}
	return nil, nil
}

type subscriptionFees struct {
	payment              bool
	amount               float64
	category             *string
	metaCategories, meta *string
	membershipSet        bool // membership_fee is not None
	membershipFloat      bool // a float (otherwise the int 0)
	membershipFee        float64
}

func subscriptionCreateFloat(value any) (float64, error) {
	number, ok, server := dto.SubscriptionCreateFloat(value)
	if server || !ok {
		// float() raises (ValueError, TypeError or OverflowError): the view does not handle it.
		return 0, domain.ErrSubscriptionCreateServer
	}
	return number, nil
}

// fees is the enable_quotes_management block: get_subscription_fee,
// get_membership_fee, the payment category and whether a payment is due.
func (s *SubscriptionCreates) fees(ctx context.Context, tx domain.SubscriptionCreateTransaction, data domain.RequestObject,
	settings *domain.SubscriptionCreateSettings, kind int64) (subscriptionFees, error) {
	if !settings.QuotesEnabled {
		return subscriptionFees{membershipSet: true}, nil
	}
	fees := subscriptionFees{}
	var fee any = json.Number("0")
	var payMeta domain.RequestObject
	if kind == 1 || kind == 2 {
		fee = json.Number(settings.SubscriptionFee)
		if settings.MultipleSubscriptionFee {
			// Any exception in the plan lookup falls back to the association's fee.
			planID, present := data.Lookup("plan_id")
			if present {
				plan, err := subscriptionCreatePlan(settings.SubscriptionPlans, planID)
				if errors.Is(err, domain.ErrSubscriptionCreateNotPorted) {
					return fees, err
				}
				if err == nil && plan != nil {
					planFee, hasFee := plan.Lookup("subscription_fee")
					name, hasName := plan.Lookup("name")
					id, _ := plan.Lookup("id")
					if hasFee && hasName {
						fee = planFee
						payMeta = domain.RequestObject{{Name: "subscription_data", Value: domain.RequestObject{
							{Name: "subscription_fee", Value: planFee}, {Name: "name", Value: name}, {Name: "id", Value: id}}}}
					}
				}
			}
		}
	}
	membership, metaCategories, err := s.membershipFee(ctx, tx, data, settings, kind)
	if err != nil {
		return fees, err
	}
	amount, err := subscriptionCreateFloat(fee)
	if err != nil {
		return fees, err
	}
	if membership != nil {
		amount += *membership
		fees.membershipSet, fees.membershipFloat, fees.membershipFee = true, true, *membership
	}
	fees.amount = amount
	category, err := tx.Category(ctx, settings.DefaultPaymentCategory, settings.AssociationID)
	if err != nil {
		return fees, err
	}
	fees.category = category
	if metaCategories != nil {
		text := string(dto.PythonJSONDumps(metaCategories))
		fees.metaCategories = &text
	}
	if payMeta != nil {
		text := string(dto.PythonJSONDumps(payMeta))
		fees.meta = &text
	}
	fees.payment = amount > 0 || settings.ShowZeroPayments
	return fees, nil
}

func (s *SubscriptionCreates) membershipCategory(ctx context.Context, tx domain.SubscriptionCreateTransaction,
	settings *domain.SubscriptionCreateSettings) (string, error) {
	category, err := tx.Category(ctx, settings.DefaultPaymentCategory, settings.AssociationID)
	if err != nil {
		return "", err
	}
	if category == nil {
		// str(None.payment_category_id)
		return "", domain.ErrSubscriptionCreateServer
	}
	return *category, nil
}

// membershipFee is get_membership_fee: the fee (nil for None) and the
// meta_payment_categories list.
func (s *SubscriptionCreates) membershipFee(ctx context.Context, tx domain.SubscriptionCreateTransaction, data domain.RequestObject,
	settings *domain.SubscriptionCreateSettings, kind int64) (*float64, []any, error) {
	if kind == 1 {
		return nil, nil, nil
	}
	planID, present := data.Lookup("membership_plan_id")
	if settings.MultipleMembershipFee && !present {
		return nil, nil, &domain.SubscriptionCreateInvalid{Detail: map[string]string{"msg": "Membership plan id is required.", "code": "400"}}
	}
	feeZero := settings.MembershipFee == nil
	if !feeZero {
		value, _ := new(big.Float).SetString(*settings.MembershipFee)
		feeZero = value == nil || value.Sign() == 0
	}
	if present && planID == nil && feeZero {
		return nil, nil, nil
	}
	var plan domain.RequestObject
	if settings.MultipleMembershipFee {
		var err error
		if plan, err = subscriptionCreatePlan(settings.MembershipPlans, planID); err != nil {
			return nil, nil, err
		}
		if plan == nil {
			return nil, nil, &domain.SubscriptionCreateInvalid{Detail: map[string]string{"msg": "Membership plan not found.", "code": "400"}}
		}
		value, hasFee := plan.Lookup("membership_fee")
		name, hasName := plan.Lookup("name")
		id, _ := plan.Lookup("id")
		if !hasFee || !hasName {
			return nil, nil, domain.ErrSubscriptionCreateServer
		}
		fee, err := subscriptionCreateFloat(value)
		if err != nil {
			return nil, nil, err
		}
		switch kind {
		case 2:
			category, err := s.membershipCategory(ctx, tx, settings)
			if err != nil {
				return nil, nil, err
			}
			return &fee, []any{domain.RequestObject{{Name: "payment_category_id", Value: category}, {Name: "amount", Value: fee},
				{Name: "subject", Value: json.Number("0")}, {Name: "title", Value: name}, {Name: "id", Value: dto.PythonStr(id)}}}, nil
		case 3:
			return &fee, nil, nil
		}
		return nil, nil, nil
	}
	if settings.MembershipFee == nil {
		return nil, nil, nil
	}
	fee, err := subscriptionCreateFloat(json.Number(*settings.MembershipFee))
	if err != nil {
		return nil, nil, err
	}
	switch kind {
	case 3:
		return &fee, nil, nil
	case 2:
		category, err := s.membershipCategory(ctx, tx, settings)
		if err != nil {
			return nil, nil, err
		}
		return &fee, []any{domain.RequestObject{{Name: "payment_category_id", Value: category}, {Name: "amount", Value: fee},
			{Name: "subject", Value: json.Number("0")}, {Name: "title", Value: "Quota tesseramento"}, {Name: "id", Value: nil}}}, nil
	}
	return nil, nil, nil
}

// afterCommit runs the transaction.on_commit callbacks (robust: failures are
// logged) in registration order: new_subscription's check_workflows_trigger,
// print_document_subscription and the notification.
func (s *SubscriptionCreates) afterCommit(ctx context.Context, identity domain.ResolvedIdentity, created *subscriptionCreated,
	request SubscriptionCreateRequest) {
	work, cancel := context.WithTimeout(context.WithoutCancel(ctx), subscriptionCreateAfterCommitTimeout)
	defer cancel()
	if created.welcome != nil {
		if _, err := s.tasks.Publish(work, *created.welcome); err != nil {
			s.logger.Error("welcome e-mail dispatch failed", "error", err)
		}
	}
	if _, err := s.tasks.Publish(work, celery.Task{Name: "check_workflows_trigger", Kwargs: celery.Dict{
		{Key: "trigger_type", Value: "new_subscription"},
		{Key: "sport_association_id", Value: uuid.MustParse(created.association)},
		{Key: "subscription_id", Value: uuid.MustParse(created.id)},
	}}); err != nil {
		s.logger.Error("new_subscription callback failed", "error", err)
	}
	var authorization any
	if request.Authorization != nil {
		authorization = *request.Authorization
	}
	if _, err := s.tasks.Publish(work, celery.Task{Name: "print_document_subscription", Args: []any{created.id, authorization}}); err != nil {
		s.logger.Error("print_document_subscription dispatch failed", "error", err)
	}
	if s.notifications != nil {
		message := fmt.Sprintf("Nuova Iscrizione aggiunta per %s.", created.associateName)
		target := identity.ScopeUser.ID
		if created.notifyUser != "" {
			target = created.notifyUser
		}
		send := func() error {
			if created.validated != nil {
				return s.notifications.SendValidated(work, target, "subscription", message, created.association, *created.validated)
			}
			return s.notifications.Send(work, target, "subscription", message, created.association, request.Audit)
		}
		if err := send(); err != nil {
			s.logger.Error("subscription notification failed", "error", err)
		}
	}
	if created.athleteMail != nil {
		if _, err := s.tasks.Publish(work, *created.athleteMail); err != nil {
			s.logger.Error("new subscription e-mail dispatch failed", "error", err)
		}
	}
}

// subscriptionPythonDateTime is str() of a naive datetime at midnight.
func subscriptionPythonDateTime(date time.Time) string {
	return fmt.Sprintf("%04d-%02d-%02d 00:00:00", date.Year(), int(date.Month()), date.Day())
}
