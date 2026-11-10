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
	"unicode"

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
}

// SubscriptionCreateRequest is subscription_add's input after admission.
type SubscriptionCreateRequest struct {
	Body          any
	Form          bool
	Mode          *string // request.GET.get('m')
	Authorization *string // request.headers.get('authorization')
	Group         string
	Audit         domain.LoginAuditContext
}

// SubscriptionCreates ports subscription_add and create_subscription
// (subscription-create.md).
type SubscriptionCreates struct {
	store         domain.SubscriptionCreateStore
	tasks         celery.Publisher
	notifications *SubscriptionNotifier
	now           func() time.Time
	logger        *slog.Logger
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

// Add follows subscription_add for authenticated owners and collaborators.
func (s *SubscriptionCreates) Add(ctx context.Context, identity domain.ResolvedIdentity, request SubscriptionCreateRequest) (*SubscriptionCreateResult, error) {
	user := identity.ScopeUser
	// Anonymous requests (attributed to the association named in the body)
	// and form bodies are not ported.
	if user == nil || request.Form || strings.TrimSpace(request.Group) != "" {
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	request.Audit = paymentCategoryAuditContext(identity, request.Audit)
	// _prepare_subscription_request logs data.get('is_family_wizard'): only a dict passes.
	data, ok := request.Body.(domain.RequestObject)
	if !ok {
		return nil, domain.ErrSubscriptionCreateServer
	}
	quick := request.Mode != nil && *request.Mode == "only_associate"
	if objectHas(data, "sport_association") {
		// Athlete requests and associations named in the body are not ported.
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	if quick {
		if user.Role == domain.RoleAssociation {
			tx, err := s.store.BeginSubscriptionCreate(ctx)
			if err != nil {
				return nil, err
			}
			association, err := tx.ActiveAssociationOf(ctx, user.ID)
			_ = tx.Rollback()
			if err != nil {
				return nil, err
			}
			if association == nil {
				// .first().sport_association_id on None.
				return nil, domain.ErrSubscriptionCreateServer
			}
			if objectHas(data, "associate") {
				// The quick subscription of an existing associate is not ported.
				return nil, domain.ErrSubscriptionCreateNotPorted
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
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	return s.create(ctx, identity, data, request)
}

// refuse rejects the inputs whose create_subscription paths are not ported,
// before anything is read or written.
func subscriptionCreateRefuse(data domain.RequestObject) error {
	if value, _ := data.Lookup("preregistration"); value == true {
		return domain.ErrSubscriptionCreateNotPorted
	}
	accountValue, _ := data.Lookup("new_user_account")
	if account, ok := accountValue.(domain.RequestObject); ok {
		if value, present := account.Lookup("new_member"); present {
			if flag, code := dtoBoolean(value); code == "" && flag {
				// A new member account and its welcome e-mail are not ported.
				return domain.ErrSubscriptionCreateNotPorted
			}
		}
	}
	signatureValue, _ := data.Lookup("signature")
	if signature, ok := signatureValue.(domain.RequestObject); ok {
		if parsed, invalid := dto.ParseSignatureRequest(signature); invalid == nil && parsed.Signed {
			// Uploading the signature to storage is not ported.
			return domain.ErrSubscriptionCreateNotPorted
		}
	}
	if medical, ok := data.Lookup("medical_certificate"); ok {
		if object, isObject := medical.(domain.RequestObject); isObject {
			// The expiration update and the placeholder certificate image are not
			// ported; a None date raises inside their try blocks and changes nothing.
			if expiration, present := object.Lookup("certificate_expring_date"); present && expiration != nil {
				return domain.ErrSubscriptionCreateNotPorted
			}
		}
	}
	if custom, ok := data.Lookup("custom_data"); ok {
		if object, isObject := custom.(domain.RequestObject); isObject {
			if kind, _ := object.Lookup("type_of_associate"); subscriptionTruthy(kind) {
				// The custom_data icontains filter is not ported.
				return domain.ErrSubscriptionCreateNotPorted
			}
		}
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
}

func (a subscriptionAudit) associate(ctx context.Context, key string, action int, created bool, changes map[string][2]string, association *string,
	path string) error {
	if !created && len(changes) == 0 {
		return nil
	}
	return a.tx.Audit(ctx, domain.SubscriptionEditAudit{Model: "associate", Key: key, Action: action, Created: created, Changes: changes,
		Association: association, Path: path, Context: a.context, At: a.now().UTC()})
}

func (a subscriptionAudit) created(ctx context.Context, model, representation, table, column, key string, overrides map[string][2]string,
	association *string, path string) error {
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
}

func (s *SubscriptionCreates) create(ctx context.Context, identity domain.ResolvedIdentity, data domain.RequestObject,
	request SubscriptionCreateRequest) (*SubscriptionCreateResult, error) {
	if err := subscriptionCreateRefuse(data); err != nil {
		return nil, err
	}
	tx, err := s.store.BeginSubscriptionCreate(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	created, err := s.createSubscription(ctx, tx, identity, data, request)
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	s.afterCommit(ctx, identity, created, request)
	return &created.result, nil
}

func (s *SubscriptionCreates) createSubscription(ctx context.Context, tx domain.SubscriptionCreateTransaction, identity domain.ResolvedIdentity,
	data domain.RequestObject, request SubscriptionCreateRequest) (*subscriptionCreated, error) {
	server := domain.ErrSubscriptionCreateServer
	user := identity.ScopeUser
	now := s.now().UTC()
	today := subscriptionCreateDay(now)
	associateValue, present := data.Lookup("associate_data")
	if !present {
		return nil, pythonKeyError("associate_data")
	}
	associate, ok := associateValue.(domain.RequestObject)
	if !ok {
		return nil, pythonSubscriptError(associateValue)
	}
	bornValue, present := associate.Lookup("born_date")
	if !present {
		return nil, pythonKeyError("born_date")
	}
	if _, isText := bornValue.(string); !isText && bornValue != nil {
		// str() of numbers and containers through dateutil is not ported.
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	born, result := dto.DateutilDate(bornValue)
	if result == dto.DateutilUnsupported {
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	if result != dto.DateutilParsed {
		if bornValue == nil {
			return nil, &domain.SubscriptionCreateFailure{Text: "Unknown string format: None"}
		}
		if text := bornValue.(string); strings.TrimFunc(text, unicode.IsSpace) == "" {
			return nil, &domain.SubscriptionCreateFailure{Text: "String does not contain a date: " + text}
		}
		return nil, server
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
	if user.Role == domain.RoleAthlete {
		return nil, &domain.PermissionDenied{Detail: "User is not a sport association"}
	}
	settings, count, err := tx.Settings(ctx, user.ID)
	if err != nil {
		return nil, err
	}
	if count != 1 {
		return nil, subscriptionAssociationGet(count)
	}
	association := settings.AssociationID
	if named, present := data.Lookup("sport_association"); present {
		// User.objects.get(username=...) and its SportAssociation; a miss keeps request.user's.
		if err := s.namedAssociation(ctx, tx, named, association); err != nil {
			return nil, err
		}
	}
	associate = objectSet(associate, "sport_association", association)
	associate = objectSet(associate, "user", user.ID)
	data = objectSet(data, "associate_data", associate)
	if tutor != nil {
		tutor = objectSet(tutor, "sport_association", association)
		tutor = objectSet(tutor, "user", user.ID)
		data = objectSet(data, "associate_tutor_data", tutor)
	}
	rule := domain.CalendarRule{StartDay: settings.StartDay, StartMonth: settings.StartMonth, CustomEnd: settings.CustomEnd,
		EndDay: settings.EndDay, EndMonth: settings.EndMonth}
	season, err := SeasonRange(ctx, now, rule)
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
	existing, err := tx.Existing(ctx, association, names[0], names[1], names[2], subscriptionCreateDay(season.Start), subscriptionCreateDay(season.End))
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
	audit := subscriptionAudit{tx: tx, context: request.Audit, now: s.now}
	ownAssociation := &association
	saved, err := s.saveAssociate(ctx, tx, audit, associate, association, nil)
	if err != nil {
		return nil, err
	}
	var savedTutor *domain.SubscriptionEditAssociate
	if saved.IsMinor && tutor != nil {
		savedTutor, err = s.saveAssociate(ctx, tx, audit, tutor, association, &saved.ID)
		if err != nil {
			return nil, err
		}
		if savedTutor != nil {
			old := *savedTutor
			savedTutor.UserID = &user.ID
			if err = tx.SaveAssociate(ctx, *savedTutor); err != nil {
				return nil, err
			}
			if err = audit.associate(ctx, savedTutor.ID, 1, false, associateChanges(old, *savedTutor), ownAssociation, "direct"); err != nil {
				return nil, err
			}
			relations, err := tx.Relations(ctx, saved.ID)
			if err != nil {
				return nil, err
			}
			if len(relations) > 0 {
				// An associate that already has tutors is not ported.
				return nil, domain.ErrSubscriptionCreateNotPorted
			}
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
	old := *saved
	saved.UserID = &user.ID
	if err = tx.SaveAssociate(ctx, *saved); err != nil {
		return nil, err
	}
	if err = audit.associate(ctx, saved.ID, 1, false, associateChanges(old, *saved), ownAssociation, "direct"); err != nil {
		return nil, err
	}
	signatureValue, _ := data.Lookup("signature")
	if _, invalid := dto.ParseSignatureRequest(signatureValue); invalid != nil {
		return nil, &domain.SubscriptionCreateInvalid{Fields: invalid}
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
		found, owner, err := tx.Certificate(ctx, id)
		if err != nil {
			return nil, err
		}
		if found {
			medical = &id
			if owner == nil {
				if err = tx.SaveCertificateUser(ctx, id, settings.OwnerID); err != nil {
					return nil, err
				}
				actorAssociation, err := s.actorAssociation(ctx, tx, request.Audit)
				if err != nil {
					return nil, err
				}
				if err = audit.updated(ctx, "medicalcertificate", "MedicalCertificate", id, map[string][2]string{"user": {"None", settings.OwnerID}},
					actorAssociation, "actor.sport_association"); err != nil {
					return nil, err
				}
			}
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
		amount, ok := dto.SubscriptionCreateDecimal(fees.amount)
		if !ok {
			return nil, domain.ErrSubscriptionCreateNotPorted
		}
		day := calendarTime(today)
		reusable, tied, err := tx.ReusablePayments(ctx, saved.ID, association, amount, day)
		if err != nil {
			return nil, err
		}
		if tied {
			return nil, domain.ErrSubscriptionCreateNotPorted
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
			row := domain.SubscriptionCreatePayment{ID: id, AssociationID: association, AssociateID: saved.ID, UserID: user.ID, Amount: amount,
				CategoryID: category, AccountID: account, MetaCategories: fees.metaCategories, Meta: fees.meta, PaymentDate: day, Created: s.now().UTC()}
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
	// Subscription.objects.create: subscription_exists on the saved associate
	// for yesterday..tomorrow, then Subscription.save()'s dates.
	duplicate, err := tx.Existing(ctx, association, saved.FirstName, saved.LastName, saved.TaxCode,
		subscriptionCreateDay(calendarTime(today).AddDate(0, 0, -1)), subscriptionCreateDay(calendarTime(today).AddDate(0, 0, 1)))
	if err != nil {
		return nil, err
	}
	if duplicate != nil {
		return nil, domain.ErrSubscriptionCreateDuplicate
	}
	createdAt := s.now().UTC()
	dates, err := ResolveSubscriptionDates(ctx, createdAt, domain.SubscriptionDateRule{Season: rule, Duration: settings.SubscriptionDuration},
		domain.SubscriptionDates{})
	if err != nil {
		return nil, subscriptionCreateCalendar(err)
	}
	subscriptionID := uuid.NewString()
	row := domain.SubscriptionCreateRow{ID: subscriptionID, AssociationID: association, AssociateID: saved.ID, UserID: user.ID,
		Type: subscriptionType, Role: role, Status: 1, MedicalID: medical, PaymentID: paymentID, CustomData: customData,
		AdditionalFields: additionalFields, Meta: meta, Number: number.String(), SubscriptionType: settings.DefaultMembershipType,
		Created: createdAt, Start: dates.Start, End: dates.End}
	if err = tx.InsertSubscription(ctx, row); err != nil {
		return nil, err
	}
	if err = audit.created(ctx, "subscription", "Subscription", "application_subscription", "subscription_id", subscriptionID, nil,
		ownAssociation, "direct"); err != nil {
		return nil, err
	}
	if subscriptionType == 2 || subscriptionType == 3 {
		membershipType := ""
		if settings.DefaultMembershipType != nil && *settings.DefaultMembershipType != "" {
			membershipType = *settings.DefaultMembershipType
		} else if membershipPlanName != nil {
			text, isText := membershipPlanName.(string)
			if !isText {
				return nil, domain.ErrSubscriptionCreateNotPorted
			}
			membershipType = text
		} else {
			membershipType = fmt.Sprintf("Tesseramento %d/%d", season.Start.Year(), season.End.Year())
		}
		var start, end *domain.CalendarDate
		from, to := subscriptionCreateDay(season.Start), subscriptionCreateDay(season.End)
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
				text, ok := dto.SubscriptionCreateDecimal(fees.membershipFee)
				if !ok {
					return nil, domain.ErrSubscriptionCreateNotPorted
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
	return &subscriptionCreated{id: subscriptionID, association: association, associateName: name,
		result: SubscriptionCreateResult{PaymentID: paymentID, Amount: amountJSON}, first: saved.FirstName, last: saved.LastName,
		tax: saved.TaxCode, start: dates.Start, end: dates.End, userID: user.ID}, nil
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
		if !dto.AssociateTaxCodeASCII(text) {
			return nil, domain.ErrSubscriptionCreateNotPorted
		}
		return objectSet(object, "sex", strings.ToUpper(text)), nil
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
	object domain.RequestObject, association string, minorOf *string) (*domain.SubscriptionEditAssociate, error) {
	isTutor := minorOf != nil
	bornValue, _ := object.Lookup("born_date")
	var born time.Time
	var bornError []string
	switch v := bornValue.(type) {
	case nil:
		if isTutor && !objectHas(object, "born_date") {
			// A tutor without born_date stores None; not ported.
			return nil, domain.ErrSubscriptionCreateNotPorted
		}
		return nil, domain.ErrSubscriptionCreateNotPorted
	case string:
		if !dto.AssociateTaxCodeASCII(v) {
			return nil, domain.ErrSubscriptionCreateNotPorted
		}
		parsed, ok := dto.SubscriptionCreateBornDate(v)
		if !ok {
			bornError = []string{dto.SubscriptionCreateDateFormat}
		}
		born = parsed
	default:
		return nil, domain.ErrSubscriptionCreateNotPorted
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
	if write.AssociateID != nil && *write.AssociateID != "" {
		return s.saveAssociateByID(ctx, tx, audit, write, association, minorOf)
	}
	ownAssociation := &association
	tax := write.Text["tax_code"]
	if tax != nil && *tax != "" {
		if !dto.AssociateTaxCodeASCII(*tax) {
			return nil, domain.ErrSubscriptionCreateNotPorted
		}
		upper := dto.AssociateUpper(*tax)
		write.Text["tax_code"] = &upper
		matches, err := tx.TaxCodeMatches(ctx, upper, ownAssociation, nil)
		if err != nil {
			return nil, err
		}
		if len(matches) > 0 {
			if len(matches) > 1 && matches[0].CreationDate.Equal(matches[1].CreationDate) {
				return nil, domain.ErrSubscriptionCreateNotPorted
			}
			row := matches[0]
			if minorOf != nil && row.ID == *minorOf {
				return nil, domain.ErrSubscriptionCreateNotPorted
			}
			old := row
			applyAssociateTutorWrite(&row, write)
			if err = tx.SaveAssociate(ctx, row); err != nil {
				return nil, err
			}
			if err = audit.associate(ctx, row.ID, 1, false, associateChanges(old, row), ownAssociation, "direct"); err != nil {
				return nil, err
			}
			return &row, nil
		}
	}
	row := domain.SubscriptionEditAssociate{ID: uuid.NewString(), Sex: "A", CreationDate: s.now().UTC()}
	applyAssociateTutorWrite(&row, write)
	if err = tx.InsertAssociate(ctx, row); err != nil {
		return nil, err
	}
	if err = audit.associate(ctx, row.ID, 0, true, nil, ownAssociation, "direct"); err != nil {
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
		if _, isFloat := value.(json.Number); isFloat && strings.ContainsAny(string(value.(json.Number)), ".eE") {
			return nil, domain.ErrSubscriptionCreateNotPorted
		}
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
	if server {
		return 0, domain.ErrSubscriptionCreateServer
	}
	if !ok {
		return 0, domain.ErrSubscriptionCreateNotPorted
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
		send := func() error {
			if created.validated != nil {
				return s.notifications.SendValidated(work, identity.ScopeUser.ID, "subscription", message, created.association, *created.validated)
			}
			return s.notifications.Send(work, identity.ScopeUser.ID, "subscription", message, created.association, request.Audit)
		}
		if err := send(); err != nil {
			s.logger.Error("subscription notification failed", "error", err)
		}
	}
}
