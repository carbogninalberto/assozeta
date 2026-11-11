package service

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// CourseEnrolment ports CourseSubscriptionViewSet.add (atomic),
// course_overview_add and course_installment_make_payment (not atomic: work
// saved before a late error stays, so those paths commit before answering).
type CourseEnrolment struct {
	store    domain.CourseEnrolmentStore
	now      func() time.Time
	newID    func() string
	renderer *CourseSubscriptions
}

func NewCourseEnrolment(store domain.CourseEnrolmentStore, now func() time.Time, newID func() string) *CourseEnrolment {
	return &CourseEnrolment{store: store, now: now, newID: newID, renderer: &CourseSubscriptions{now: now}}
}

// ErrCourseEnrolmentNotPorted refuses inputs this port does not reproduce,
// before any write (see course-enrolment.md).
var ErrCourseEnrolmentNotPorted = refusal.New("course enrolment input is not ported")

// CourseEnrolmentInvalid is the serializer's 400: one field map, or one per
// item for a list body.
type CourseEnrolmentInvalid struct {
	Fields map[string]any
	Items  []map[string]any
	Many   bool
}

func (e *CourseEnrolmentInvalid) Error() string { return "invalid course enrolment input" }

// CourseEnrolmentRequest carries the request inputs the views read.
type CourseEnrolmentRequest struct {
	Body        any
	Form        bool
	GroupHeader string
	Audit       domain.LoginAuditContext
}

const courseMembershipType = 3

var courseEnrolmentServer = domain.ErrCourseSubscriptionServer

func (s *CourseEnrolment) begin(ctx context.Context) (domain.CourseEnrolmentTransaction, error) {
	return s.store.BeginCourseEnrolment(ctx)
}

// courseEnrolmentScoped is scoped_queryset's association filter during an
// association-scoped impersonation session: rows of another (or no)
// association are not found. The scoped request guard has already checked
// the referenced ids with the same filters (family visibility included).
func courseEnrolmentScoped(identity domain.ResolvedIdentity, associations ...*string) bool {
	scope := identity.ImpersonationAssociationID
	if scope == "" {
		return true
	}
	for _, association := range associations {
		if association == nil || *association != scope {
			return false
		}
	}
	return true
}

func (s *CourseEnrolment) moment() time.Time { return s.now().UTC().Truncate(time.Microsecond) }

func (s *CourseEnrolment) auditContext(ctx context.Context, tx domain.CourseEnrolmentTransaction, identity domain.ResolvedIdentity,
	request CourseEnrolmentRequest) (domain.CourseSubscriptionAudit, error) {
	actor, err := tx.ActorAssociation(ctx, identity.Actor.ID)
	if err != nil {
		return domain.CourseSubscriptionAudit{}, err
	}
	return domain.CourseSubscriptionAudit{Context: paymentCategoryAuditContext(identity, request.Audit), ActorAssociation: actor,
		At: s.moment(), Group: supplierGroup(request.GroupHeader), GroupRaw: rawGroupSpelling(request.GroupHeader)}, nil
}

// rawGroupSpelling is the X-Group-Id text as sent when the group-aware
// create stores it in a spelling other than the canonical one; auditlog
// records the created row's group as sent.
func rawGroupSpelling(header string) string {
	if group := supplierGroup(header); header != "" && group != nil && *group != header {
		return header
	}
	return ""
}

// createGroup is GroupAwareManager.create's raw X-Group-Id: a value the
// UUIDField rejects, or an unknown group (deferred foreign key), fails.
func createGroup(ctx context.Context, tx domain.CourseEnrolmentTransaction, header string) (*string, error) {
	if header == "" {
		return nil, nil
	}
	group := supplierGroup(header)
	if group == nil {
		return nil, courseEnrolmentServer
	}
	exists, err := tx.GroupExists(ctx, *group)
	if err != nil {
		return nil, err
	}
	if !exists {
		return nil, courseEnrolmentServer
	}
	return group, nil
}

func decimalPositive(text *string) (bool, bool) {
	if text == nil {
		return false, false
	}
	value, ok := new(big.Rat).SetString(*text)
	return ok && value.Sign() > 0, ok
}

func floatFeeRepr(text string) string {
	return strings.Replace(text, "'membership_fee': Decimal('0.00')", "'membership_fee': 0.0", 1)
}

// quoteAmount is float(quote['amount'].replace(',', '.')): the numeric(9,2)
// value PostgreSQL stores for DecimalField.to_python(float) (Context(prec=9)
// create_decimal_from_float, then the column's half-away-from-zero rounding)
// and str() of the float. float() failures, NaN (to_python's
// ValidationError), values the column cannot hold and negative amounts (the
// amount >= 0 check constraint) raise (500).
func quoteAmount(value any) (stored, memory string, err error) {
	text, ok := value.(string)
	if !ok {
		// .replace on a non-string: AttributeError.
		return "", "", courseEnrolmentServer
	}
	number, ok := dto.PythonFloatParse(strings.ReplaceAll(text, ",", "."))
	if !ok || math.IsNaN(number) || math.IsInf(number, 0) {
		return "", "", courseEnrolmentServer
	}
	stored, ok = courseEnrolmentNumeric92(number)
	if !ok || strings.HasPrefix(stored, "-") {
		// Overflow, or the payment_amount_positive check constraint (amount >= 0).
		return "", "", courseEnrolmentServer
	}
	return stored, dto.PythonFloatStr(number), nil
}

// courseEnrolmentNumeric92 rounds Context(prec=9).create_decimal_from_float
// to cents half away from zero (numeric's rounding); ok is false when the
// result needs more than seven integer digits (numeric field overflow).
func courseEnrolmentNumeric92(number float64) (string, bool) {
	exact, ok := new(big.Rat).SetString(dto.DecimalFromFloat9(number))
	if !ok {
		return "", false
	}
	cents := new(big.Rat).Mul(exact, big.NewRat(100, 1))
	negative := cents.Sign() < 0
	cents.Abs(cents)
	quotient, remainder := new(big.Int).QuoRem(cents.Num(), cents.Denom(), new(big.Int))
	if new(big.Int).Mul(remainder, big.NewInt(2)).Cmp(cents.Denom()) >= 0 {
		quotient.Add(quotient, big.NewInt(1))
	}
	if quotient.Cmp(big.NewInt(1_000_000_000)) >= 0 {
		return "", false
	}
	digits := quotient.String()
	for len(digits) < 3 {
		digits = "0" + digits
	}
	result := digits[:len(digits)-2] + "." + digits[len(digits)-2:]
	if negative && quotient.Sign() != 0 {
		result = "-" + result
	}
	return result, true
}

func amountJSON(text string) json.Number {
	number, _ := strconv.ParseFloat(text, 64)
	return json.Number(dto.PythonFloatStr(number))
}

func enrolmentJSON(value any) *string { return dto.CourseWritesJSONText(value) }

// --- course-subscriptions/add ------------------------------------------------------

type enrolmentItem struct {
	write  dto.CourseSubscriptionWrite
	course domain.CourseWriteRow
}

// associationOf is User.sport_association.
func (s *CourseEnrolment) associationOf(ctx context.Context, tx domain.CourseEnrolmentTransaction, identity domain.ResolvedIdentity) (string, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return "", courseSubscriptionNotAssociation
	}
	association, err := tx.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if errors.Is(err, domain.ErrAssociationNotFound) {
		return "", courseEnrolmentServer
	}
	return association, err
}

// validateItem is one serializer child: the fields, the course lookup, the
// primary key's UniqueValidator, then validate() for memberships.
func (s *CourseEnrolment) validateItem(ctx context.Context, tx domain.CourseEnrolmentTransaction, identity domain.ResolvedIdentity, item any,
	group *string) (*enrolmentItem, map[string]any, error) {
	write, invalid := dto.ParseCourseSubscriptionWrite(item, false)
	object, isObject := item.(domain.RequestObject)
	if !isObject {
		return nil, invalid, nil
	}
	if invalid == nil {
		invalid = map[string]any{}
	}
	for _, name := range []string{"course", "subscription_id"} {
		if _, present := object.Lookup(name); !present {
			invalid[name] = []string{dto.PaymentCategoryValidationRequired}
		}
	}
	var course *domain.CourseWriteRow
	if write.CourseSet {
		found, err := tx.Course(ctx, write.Course, nil)
		if err != nil {
			return nil, nil, err
		}
		if found == nil {
			invalid["course"] = []string{"related-does-not-exist:" + write.CourseRaw}
		}
		course = found
	}
	if write.ID != nil {
		taken, err := tx.RowIDLive(ctx, *write.ID)
		if err != nil {
			return nil, nil, err
		}
		if taken {
			invalid["course_subscription_id"] = []string{dto.CourseSubscriptionUnique}
		}
	}
	if len(invalid) > 0 {
		return nil, invalid, nil
	}
	if course.CourseType == courseMembershipType {
		errs, err := s.validateMembership(ctx, tx, identity, *course, write, group)
		if err != nil || errs != nil {
			return nil, errs, err
		}
	}
	return &enrolmentItem{write: write, course: *course}, nil, nil
}

// validateMembership is the serializer's validate() on create.
func (s *CourseEnrolment) validateMembership(ctx context.Context, tx domain.CourseEnrolmentTransaction, identity domain.ResolvedIdentity,
	course domain.CourseWriteRow, write dto.CourseSubscriptionWrite, group *string) (map[string]any, error) {
	errs := map[string]any{}
	athlete := identity.ScopeUser.Role == domain.RoleAthlete
	if !athlete {
		association, err := s.associationOf(ctx, tx, identity)
		if err != nil {
			return nil, err
		}
		if course.AssociationID == nil || *course.AssociationID != association {
			errs["course"] = []string{courseSubscriptionForeignCourse}
		}
	}
	visible, err := tx.SubscriptionInAssociation(ctx, *write.SubscriptionID, course.AssociationID, group)
	if err != nil {
		return nil, err
	}
	if !visible {
		errs["subscription_id"] = []string{"Seleziona un tesserato della stessa associazione del corso."}
	}
	start, end := write.Times["billed_from"], write.Times["billed_until"]
	if start == nil && !athlete {
		errs["billed_from"] = []string{"Inserisci una data di inizio valida."}
	}
	if end == nil && !athlete {
		errs["billed_until"] = []string{"Inserisci una data di fine valida."}
	}
	if start != nil && end != nil && end.Before(*start) {
		errs["billed_until"] = []string{"La data di fine non può precedere la data di inizio."}
	}
	frequency, present := write.Ints["billed_frequency"]
	if !present {
		frequency = 1
	}
	if frequency < 1 || frequency > 12 {
		errs["billed_frequency"] = []string{"La durata deve essere compresa tra 1 e 12 mesi."}
	}
	if write.FeeSet && (write.Fee == nil || dto.CourseSubscriptionDecimalNegative(*write.Fee)) {
		errs["membership_fee"] = []string{"La quota deve essere un importo maggiore o uguale a zero."}
	}
	if len(errs) > 0 {
		return errs, nil
	}
	return nil, nil
}

func enrolmentMemory(write dto.CourseSubscriptionWrite) dto.CourseSubscriptionMemory {
	memory := dto.CourseSubscriptionMemory{Times: map[string]bool{"creation_date": write.CreationDate != nil}, QuoteSet: write.QuoteSet,
		Quote: write.Quote}
	for name, value := range write.Times {
		memory.Times[name] = value != nil
	}
	return memory
}

func enrolmentRepr(row domain.CourseSubscriptionRow, memory dto.CourseSubscriptionMemory, floatFee bool) string {
	text := dto.CourseSubscriptionRepr(row, memory)
	if floatFee {
		text = floatFeeRepr(text)
	}
	return text
}

type enrolmentRow struct {
	row          domain.CourseSubscriptionRow
	memory       dto.CourseSubscriptionMemory
	floatFee     bool
	subscription domain.CourseWriteSubscription
}

func (r *enrolmentRow) save() domain.CourseEnrolmentRowSave {
	return domain.CourseEnrolmentRowSave{Row: r.row, FloatFee: r.floatFee, Representation: enrolmentRepr(r.row, r.memory, r.floatFee)}
}

// serializerCreate is CourseSubscriptionOverviewSerializer.create().
func (s *CourseEnrolment) serializerCreate(ctx context.Context, tx domain.CourseEnrolmentTransaction, item enrolmentItem, request CourseEnrolmentRequest,
	audit domain.CourseSubscriptionAudit) (*enrolmentRow, error) {
	write, course := item.write, item.course
	subscription, err := tx.Subscription(ctx, *write.SubscriptionID, audit.Group)
	if err != nil {
		return nil, err
	}
	if subscription == nil {
		// Subscription.objects.get: DoesNotExist.
		return nil, courseEnrolmentServer
	}
	flag := func(name string, fallback bool) bool {
		if value, present := write.Bools[name]; present {
			return value
		}
		return fallback
	}
	integer := func(name string) int {
		if value, present := write.Ints[name]; present {
			return value
		}
		return 1
	}
	row := domain.CourseSubscriptionRow{ID: s.newID(), CourseID: course.ID, Paid: flag("paid", false), CreationDate: s.moment(),
		SubscriptionID: &subscription.ID, MultiPayments: flag("multi_payments", false), OneFeePayment: flag("one_fee_payment", false),
		Type: course.CourseType, BilledFrequency: integer("billed_frequency"), BilledFrom: write.Times["billed_from"],
		BilledUntil: write.Times["billed_until"], BilledFromDayOfMonth: integer("billed_from_day_of_month"),
		MembershipActive: flag("membership_active", true), AutoRenewal: flag("auto_renewal", false), CourseAssociationID: course.AssociationID,
		CourseTitle: course.Title, CourseType: course.CourseType}
	if write.ID != nil {
		row.ID = *write.ID
	}
	if write.CreationDate != nil {
		row.CreationDate = *write.CreationDate
	}
	if write.QuoteSet {
		row.MultipleQuote = enrolmentJSON(write.Quote)
	}
	zero := "0.00"
	created := &enrolmentRow{row: row, memory: enrolmentMemory(write), floatFee: !write.FeeSet, subscription: *subscription}
	created.row.MembershipFee = &zero
	if write.FeeSet {
		created.row.MembershipFee = write.Fee
	}
	overrides := map[string][2]string{}
	if created.floatFee {
		overrides["membership_fee"] = [2]string{"None", "0.0"}
	}
	if err = tx.CreateRow(ctx, created.row, overrides, enrolmentRepr(created.row, created.memory, created.floatFee), audit); err != nil {
		return nil, err
	}
	var events []any
	plannedOnly := false
	switch {
	case dto.PythonTruthy(write.Events):
		var ok bool
		if events, ok = dto.PythonIterate(write.Events); !ok {
			return nil, courseEnrolmentServer
		}
	case course.MultiPayments:
		stored, err := dto.CourseWritesStoredJSON(course.Events)
		if err != nil {
			return nil, err
		}
		var ok bool
		if events, ok = dto.PythonIterate(stored); !ok || stored == nil {
			return nil, courseEnrolmentServer
		}
		plannedOnly = true
	}
	day := created.row.CreationDate.UTC()
	day = time.Date(day.Year(), day.Month(), day.Day(), 0, 0, 0, 0, time.UTC)
	for _, item := range events {
		event, invalid, err := dto.ValidateCourseEvent(item, courseEventTaken(ctx, tx, request.GroupHeader))
		if err != nil {
			return nil, err
		}
		if invalid != nil {
			return nil, &CourseWritesEventInvalid{Fields: invalid}
		}
		if plannedOnly && event.Date.Before(day) {
			if course.AssociationID == nil {
				return nil, courseEnrolmentServer
			}
			user, err := tx.AssociationUser(ctx, *course.AssociationID)
			if err != nil || user == nil {
				return nil, errors.Join(err, courseEnrolmentServer)
			}
			plan, err := tx.UserFullInstallmentsPlan(ctx, *user)
			if err != nil {
				return nil, err
			}
			if !plan {
				continue
			}
		}
		if err = s.eventPayment(ctx, tx, created, course, event, request, audit); err != nil {
			return nil, err
		}
	}
	return created, nil
}

// eventPayment is the serializer's payment and installment for one event.
func (s *CourseEnrolment) eventPayment(ctx context.Context, tx domain.CourseEnrolmentTransaction, created *enrolmentRow, course domain.CourseWriteRow,
	event dto.CourseEvent, request CourseEnrolmentRequest, audit domain.CourseSubscriptionAudit) error {
	// The serializer only creates the rows (no explicit save follows).
	audit.GroupNoResave = true
	if course.AssociationID == nil {
		// instance.course.sport_association.user: AttributeError.
		return courseEnrolmentServer
	}
	user, err := tx.AssociationUser(ctx, *course.AssociationID)
	if err != nil {
		return err
	}
	title := courseWritesText(course.Title)
	text := "Rata n." + strconv.Itoa(event.ID+1) + " del " + event.Date.Format("02/01/06") + " (" + title + ")"
	meta := domain.RequestObject{{Name: "description", Value: text}, {Name: "course_id", Value: course.ID}, {Name: "course_title", Value: title},
		{Name: "course_subscription_id", Value: created.row.ID}, {Name: "payment_date", Value: event.Date.Format("2006-01-02")}}
	payment, err := s.payment(ctx, tx, request.GroupHeader, user, course.AssociationID, event.Amount, event.Amount, event.Date, text, nil, meta, nil)
	if err != nil {
		return err
	}
	payment.AssociateID = created.subscription.AssociateID
	if err = tx.CreatePayment(ctx, payment, audit); err != nil {
		return err
	}
	return tx.CreateInstallment(ctx, domain.CourseWriteInstallmentCreate{ID: s.newID(), CourseSubscription: created.row.ID, Number: event.ID,
		Amount: event.Amount, PaymentDate: event.Date, PaymentID: payment.ID, GroupID: payment.GroupID, AssociationID: course.AssociationID}, audit)
}

// payment is Payment.objects.create(...): PaymentManager.create's default
// bank account for a user, the group-aware create's X-Group-Id and
// Payment.save()'s category default.
func (s *CourseEnrolment) payment(ctx context.Context, tx domain.CourseEnrolmentTransaction, header string, user, association *string,
	amount, memory string, created time.Time, description string, category *string, meta any, course any) (domain.CourseEnrolmentPayment, error) {
	group, err := createGroup(ctx, tx, header)
	if err != nil {
		return domain.CourseEnrolmentPayment{}, err
	}
	if category == nil {
		if category, err = tx.DefaultPaymentCategory(ctx, association); err != nil {
			return domain.CourseEnrolmentPayment{}, err
		}
	}
	var account *string
	if user != nil {
		if account, err = tx.DefaultBankAccount(ctx, association); err != nil {
			return domain.CourseEnrolmentPayment{}, err
		}
	}
	return domain.CourseEnrolmentPayment{ID: s.newID(), Amount: amount, MemoryAmount: memory, CreationDate: created, Description: description,
		CategoryID: category, UserID: user, AssociationID: association, Meta: enrolmentJSON(meta), Course: enrolmentJSON(course), AccountID: account,
		GroupID: group}, nil
}

// courseLabel is the payment's course JSON and Payment.save()'s description
// from it (', '.join([label, ”]): a None label is a TypeError).
func courseLabel(label any, course string) (domain.RequestObject, string, error) {
	text, ok := label.(string)
	if !ok {
		return nil, "", courseEnrolmentServer
	}
	return domain.RequestObject{{Name: "label", Value: label}, {Name: "value", Value: course}}, text + ", ", nil
}

func titleValue(title *string) any {
	if title == nil {
		return nil
	}
	return *title
}

// addCourseSubscription is add_course_subscription().
func (s *CourseEnrolment) addCourseSubscription(ctx context.Context, tx domain.CourseEnrolmentTransaction, created *enrolmentRow, item enrolmentItem,
	association string, request CourseEnrolmentRequest, audit domain.CourseSubscriptionAudit) error {
	course, write := item.course, item.write
	user, err := tx.AssociationUser(ctx, association)
	if err != nil {
		return err
	}
	category, err := tx.CoursesCategory(ctx, association)
	if err != nil {
		return err
	}
	title := courseWritesText(course.Title)
	created.row.CourseAssociationID = course.AssociationID
	day := created.row.CreationDate.UTC()
	courseType := strconv.Itoa(course.CourseType)
	var payment *domain.CourseEnrolmentPayment
	build := func(amount, memory string, label any, meta domain.RequestObject, category *string) error {
		courseJSON, description, err := courseLabel(label, course.ID)
		if err != nil {
			return err
		}
		if category == nil {
			if category, err = tx.DefaultPaymentCategory(ctx, &association); err != nil {
				return err
			}
		}
		built, err := s.payment(ctx, tx, request.GroupHeader, user, &association, amount, memory, s.moment(), description, category, meta, courseJSON)
		if err != nil {
			return err
		}
		built.AssociateID = created.subscription.AssociateID
		payment = &built
		return nil
	}
	if course.CourseType == 1 {
		oneFee, _ := decimalPositive(&course.OneFee)
		fee, feeValid := decimalPositive(course.Fee)
		switch {
		case write.Bools["one_fee_payment"] && oneFee:
			text := "Pagamento in un'unica soluzione per " + title
			meta := domain.RequestObject{{Name: "one_fee", Value: "true"}, {Name: "description", Value: text}, {Name: "course_id", Value: course.ID},
				{Name: "course_title", Value: title}, {Name: "course_type", Value: courseType}, {Name: "payment_date", Value: day.Format("2006-01-02")}}
			if err = build(course.OneFee, course.OneFee, titleValue(course.Title), meta, category); err != nil {
				return err
			}
		case course.MultiPayments:
			created.row.MultiPayments = true
			if err = tx.SaveRow(ctx, created.save(), audit); err != nil {
				return err
			}
		case !feeValid:
			// None > 0: TypeError.
			return courseEnrolmentServer
		case fee:
			text := "Quota corso del " + day.Format("02/01/06") + " " + title
			meta := domain.RequestObject{{Name: "description", Value: text}, {Name: "course_id", Value: course.ID}, {Name: "course_title", Value: title},
				{Name: "course_type", Value: courseType}, {Name: "payment_date", Value: day.Format("2006-01-02")}}
			if err = build(*course.Fee, *course.Fee, title, meta, category); err != nil {
				return err
			}
		}
	}
	if course.CourseType == 2 && write.QuoteSet && dto.PythonTruthy(write.Quote) {
		quote, ok := write.Quote.(domain.RequestObject)
		if !ok {
			// multiple_quote.keys(): AttributeError.
			return courseEnrolmentServer
		}
		if selected, present := quote.Lookup("payment_category"); present {
			object, ok := selected.(domain.RequestObject)
			if !ok {
				return ErrCourseEnrolmentNotPorted
			}
			if value, present := object.Lookup("value"); present {
				text, ok := value.(string)
				if !ok {
					return ErrCourseEnrolmentNotPorted
				}
				// A value the UUID field rejects raises inside the try: logged, kept.
				if id, err := paymentInfoParseUUID(text); err == nil {
					if category, err = tx.CategoryFirst(ctx, id.String()); err != nil {
						return err
					}
				}
			}
		}
		raw, present := quote.Lookup("amount")
		if !present {
			return courseEnrolmentServer
		}
		stored, memory, err := quoteAmount(raw)
		if err != nil {
			return err
		}
		quoteTitle, present := quote.Lookup("title")
		if !present {
			return courseEnrolmentServer
		}
		meta := domain.RequestObject{{Name: "multiple_quote", Value: write.Quote},
			{Name: "description", Value: "Quota " + dto.PythonStr(quoteTitle) + " a " + title}, {Name: "course_id", Value: course.ID},
			{Name: "course_title", Value: title}, {Name: "course_type", Value: courseType}}
		var chosen *string = category
		if chosen == nil {
			// Payment.save(): a None category takes the default one.
			if chosen, err = tx.DefaultPaymentCategory(ctx, &association); err != nil {
				return err
			}
		}
		if err = build(stored, memory, titleValue(course.Title), meta, chosen); err != nil {
			return err
		}
	}
	if payment == nil {
		return nil
	}
	if err = tx.CreatePayment(ctx, *payment, audit); err != nil {
		return err
	}
	// payment.amount > 0 compares the in-memory value (a quote's float).
	if positive, _ := decimalPositive(&payment.MemoryAmount); positive {
		created.row.PaymentID = &payment.ID
		return tx.SaveRow(ctx, created.save(), audit)
	}
	return nil
}

// membershipCreated is membership_created_callback.
func (s *CourseEnrolment) membershipCreated(ctx context.Context, tx domain.CourseEnrolmentTransaction, created *enrolmentRow, course domain.CourseWriteRow,
	request CourseEnrolmentRequest, audit domain.CourseSubscriptionAudit) error {
	if created.floatFee {
		return nil
	}
	positive, valid := decimalPositive(created.row.MembershipFee)
	if !valid {
		// None > 0: TypeError.
		return courseEnrolmentServer
	}
	if !positive || created.row.Type != courseMembershipType {
		return nil
	}
	from, until := created.row.BilledFrom, created.row.BilledUntil
	if from == nil || until == nil || course.AssociationID == nil {
		// strftime on None, or course.sport_association.user on None.
		return courseEnrolmentServer
	}
	user, err := tx.AssociationUser(ctx, *course.AssociationID)
	if err != nil {
		return err
	}
	start, end := from.UTC(), until.UTC()
	text := "Abbonamento: " + courseWritesText(course.Title) + " dal " + start.Format("02/01/2006") + " al " + end.Format("02/01/2006")
	fee := *created.row.MembershipFee
	meta := domain.RequestObject{{Name: "description", Value: text}, {Name: "course_id", Value: course.ID}, {Name: "course_title", Value: titleValue(course.Title)},
		{Name: "course_subscription_id", Value: created.row.ID}, {Name: "billed_from", Value: start.Format("2006-01-02")},
		{Name: "billed_until", Value: end.Format("2006-01-02")}, {Name: "amount", Value: fee}}
	payment, err := s.payment(ctx, tx, request.GroupHeader, user, course.AssociationID, fee, fee, start, text, nil, meta, nil)
	if err != nil {
		return err
	}
	payment.AssociateID = created.subscription.AssociateID
	payment.PaymentDate = &start
	if err = tx.CreatePayment(ctx, payment, audit); err != nil {
		return err
	}
	if err = tx.AddMembershipPayment(ctx, created.row.ID, payment.ID); err != nil {
		return err
	}
	return tx.SaveRow(ctx, created.save(), audit)
}

// Add follows CourseSubscriptionViewSet.add (atomic).
func (s *CourseEnrolment) Add(ctx context.Context, identity domain.ResolvedIdentity, request CourseEnrolmentRequest) ([]map[string]any, error) {
	if request.Form {
		// HTML form input (QueryDict values, JSON fields) is not ported.
		return nil, ErrCourseEnrolmentNotPorted
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	body := request.Body
	if body == nil {
		body = domain.RequestObject{}
	}
	items, many := body.([]any)
	if !many {
		items = []any{body}
	}
	group := supplierGroup(request.GroupHeader)
	plans := make([]*enrolmentItem, len(items))
	errs := make([]map[string]any, len(items))
	failed := false
	for index, item := range items {
		plan, invalid, err := s.validateItem(ctx, tx, identity, item, group)
		if err != nil {
			return nil, err
		}
		if invalid != nil {
			failed = true
			errs[index] = invalid
		} else {
			errs[index] = map[string]any{}
		}
		plans[index] = plan
	}
	if failed {
		if many {
			return nil, &CourseEnrolmentInvalid{Items: errs, Many: true}
		}
		return nil, &CourseEnrolmentInvalid{Fields: errs[0]}
	}
	audit, err := s.auditContext(ctx, tx, identity, request)
	if err != nil {
		return nil, err
	}
	athlete := identity.ScopeUser.Role == domain.RoleAthlete
	rows := make([]domain.CourseSubscriptionRow, 0, len(plans))
	quotes := make([]dto.CourseSubscriptionMemory, 0, len(plans))
	for _, plan := range plans {
		if plan.course.CourseType == courseMembershipType && athlete && !many {
			// Course.objects.get(course_id=<Course>): the UUID lookup rejects the instance.
			return nil, courseEnrolmentServer
		}
		created, err := s.serializerCreate(ctx, tx, *plan, request, audit)
		if err != nil {
			return nil, err
		}
		if plan.course.CourseType == courseMembershipType {
			err = s.membershipCreated(ctx, tx, created, plan.course, request, audit)
		} else {
			var association string
			if association, err = s.associationOf(ctx, tx, identity); err == nil {
				err = s.addCourseSubscription(ctx, tx, created, *plan, association, request, audit)
			}
		}
		if err != nil {
			return nil, err
		}
		rows = append(rows, created.row)
		quotes = append(quotes, created.memory)
	}
	renders, err := s.renderer.render(ctx, tx.Subscriptions(), rows, group)
	if err != nil {
		return nil, err
	}
	for index := range renders {
		renders[index].QuoteSet, renders[index].Quote = quotes[index].QuoteSet, quotes[index].Quote
	}
	result, err := s.renderer.items(renders)
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(); err != nil {
		return nil, err
	}
	return result, nil
}

// StockCreate is the router list route's CreateModelMixin.create on
// CourseSubscriptionViewSet: one item (many=False), perform_create with
// is_athlete=False and no transaction, then get_success_headers(
// serializer.data), which renders the validated dict: the installments method
// field filters by the dict and raises (500) after every write is kept. A
// failure after the first write keeps what was written before it.
func (s *CourseEnrolment) StockCreate(ctx context.Context, identity domain.ResolvedIdentity, request CourseEnrolmentRequest) error {
	if request.Form {
		return ErrCourseEnrolmentNotPorted
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	body := request.Body
	if body == nil {
		body = domain.RequestObject{}
	}
	plan, invalid, err := s.validateItem(ctx, tx, identity, body, supplierGroup(request.GroupHeader))
	if err != nil {
		return err
	}
	if invalid != nil {
		return &CourseEnrolmentInvalid{Fields: invalid}
	}
	audit, err := s.auditContext(ctx, tx, identity, request)
	if err != nil {
		return err
	}
	// Autocommit: the statements that ran before a failure stay.
	keep := func(failure error) error {
		if commitErr := tx.Commit(); commitErr != nil {
			return errors.Join(failure, commitErr)
		}
		return failure
	}
	created, err := s.serializerCreate(ctx, tx, *plan, request, audit)
	if err != nil {
		return keep(err)
	}
	if plan.course.CourseType == courseMembershipType {
		err = s.membershipCreated(ctx, tx, created, plan.course, request, audit)
	} else {
		var association string
		if association, err = s.associationOf(ctx, tx, identity); err == nil {
			err = s.addCourseSubscription(ctx, tx, created, *plan, association, request, audit)
		}
	}
	if err != nil {
		return keep(err)
	}
	return keep(courseEnrolmentServer)
}
