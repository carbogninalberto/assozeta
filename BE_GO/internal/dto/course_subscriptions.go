package dto

import (
	"encoding/json"
	"errors"
	"math/big"
	"regexp"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// ErrCourseSubscriptionRender is an exception while rendering the serializer
// (a 500 in Django), such as a subscription without associate.
var ErrCourseSubscriptionRender = errors.New("course subscription render failed")

var courseSubscriptionRome = func() *time.Location {
	location, err := time.LoadLocation("Europe/Rome")
	if err != nil {
		panic(err)
	}
	return location
}()

func courseSubscriptionDay(value time.Time) time.Time {
	return time.Date(value.Year(), value.Month(), value.Day(), 0, 0, 0, 0, time.UTC)
}

func courseSubscriptionDays(later, earlier time.Time) int {
	return int(courseSubscriptionDay(later).Sub(courseSubscriptionDay(earlier)).Hours() / 24)
}

func courseSubscriptionDaysText(days int) string {
	if days == 1 {
		return "1 giorno"
	}
	return strconv.Itoa(days) + " giorni"
}

// CourseSubscriptionMedicalLabel is Subscription.get_medical_label(): the age
// from date.today() (Django's TIME_ZONE, UTC), the certificate dates against
// today in Europe/Rome.
func CourseSubscriptionMedicalLabel(person domain.CourseSubscriptionPerson, now time.Time) string {
	today := courseSubscriptionDay(now.UTC())
	rome := courseSubscriptionDay(now.In(courseSubscriptionRome))
	tag := ""
	if person.MedicalID != nil && person.MedicalCompetitive != nil && *person.MedicalCompetitive {
		tag = `<span class="badge badge-info ml-1" title="Certificato Medico Agonistico">AG</span>`
	}
	born := person.Associate.BornDate
	age, days := 0, 0
	if born != nil {
		days = courseSubscriptionDays(today, *born)
		age = int(float64(days) / 365.25)
	}
	if age < 6 {
		remaining := 0
		if born != nil {
			remaining = int(6*365.25 - float64(days))
		}
		return `<span class="text-success font-weight-boldest">Esente per ` + courseSubscriptionDaysText(remaining) + `</span>` + tag
	}
	if person.MedicalID == nil || person.MedicalExpiration == nil {
		missing := max(courseSubscriptionDays(rome, person.CreationDate.In(courseSubscriptionRome)), 0)
		return `<span class="text-warning font-weight-boldest">Mancante da ` + courseSubscriptionDaysText(missing) + `</span>` + tag
	}
	expiration := courseSubscriptionDay(*person.MedicalExpiration)
	if expiration.Before(rome) {
		return `<span class="text-danger font-weight-boldest"><i class="ph-bold ph-first-aid-kit"></i>Scaduto da ` +
			courseSubscriptionDaysText(courseSubscriptionDays(rome, expiration)) + `</span>` + tag
	}
	return `<span class="text-success font-weight-boldest"><i class="ph-bold ph-first-aid-kit"></i>` +
		courseSubscriptionDaysText(courseSubscriptionDays(expiration, rome)) + ` rimanenti</span>` + tag
}

func courseSubscriptionTime(value *time.Time) any {
	if value == nil {
		return nil
	}
	return DRFUTCDateTime(*value)
}

func courseSubscriptionJSON(text *string) any {
	if text == nil {
		return nil
	}
	return json.RawMessage(*text)
}

// CourseSubscriptionRender is one row with what the serializer reads.
type CourseSubscriptionRender struct {
	Row          domain.CourseSubscriptionRow
	Person       *domain.CourseSubscriptionPerson
	Installments []domain.CourseSubscriptionInstallmentItem
	Payments     []domain.CourseSubscriptionPayment
	Carnets      []domain.CourseSubscriptionCarnet
	// QuoteSet renders the in-memory multiple_quote the serializer set.
	QuoteSet bool
	Quote    any
}

// courseSubscriptionCarnet is one get_carnets() item: lessons_left is
// meta.get('lessons_left', total) for a truthy meta (a truthy non-dict meta
// raises), lessons_used total - left when total is set.
func courseSubscriptionCarnet(row domain.CourseSubscriptionCarnet) (map[string]any, error) {
	total := 0
	if row.CarnetID != nil && row.Lessons != nil {
		total = *row.Lessons
	}
	var left any = json.Number(strconv.Itoa(total))
	if row.Meta != nil {
		meta, err := ParseReferenceJSON([]byte(*row.Meta))
		if err != nil {
			return nil, err
		}
		if PythonTruthy(meta) {
			object, ok := meta.(domain.RequestObject)
			if !ok {
				return nil, ErrCourseSubscriptionRender
			}
			if value, present := object.Lookup("lessons_left"); present {
				left = value
			}
		}
	}
	var used any = json.Number("0")
	var display any
	if total != 0 {
		switch value := left.(type) {
		case bool:
			number := total
			if value {
				number--
			}
			used = json.Number(strconv.Itoa(number))
		case json.Number:
			if strings.ContainsAny(string(value), ".eE") {
				number, err := strconv.ParseFloat(string(value), 64)
				if err != nil {
					return nil, ErrCourseSubscriptionRender
				}
				used = json.Number(PythonFloatStr(float64(total) - number))
			} else {
				number, ok := new(big.Int).SetString(string(value), 10)
				if !ok {
					return nil, ErrCourseSubscriptionRender
				}
				used = json.Number(new(big.Int).Sub(big.NewInt(int64(total)), number).String())
			}
		default:
			return nil, ErrCourseSubscriptionRender
		}
		display = PythonStr(left) + "/" + strconv.Itoa(total)
	}
	return map[string]any{"carnet_subscription_id": row.ID, "carnet_id": row.CarnetID, "title": row.Title, "lessons_total": total,
		"lessons_left": left, "lessons_used": used, "lessons_display": display,
		"creation_date": row.CreationDate.UTC().Format("02/01/2006")}, nil
}

// courseSubscriptionPayment is PaymentSerializer for a payment without its
// nested instructor and invoice (see courseSubscriptionMembershipPayment).
func courseSubscriptionPayment(row domain.CourseSubscriptionPayment) map[string]any {
	item := InvoicePartyPaymentItem(row.InvoicePartyPayment)
	if row.AssociateRow != nil {
		associate := InconsistencyAssociateFrom(*row.AssociateRow)
		item["associate"] = associate
	}
	return item
}

// CourseSubscriptionItem is CourseSubscriptionOverviewSerializer(instance).data.
func CourseSubscriptionItem(render CourseSubscriptionRender, now time.Time) (map[string]any, error) {
	row := render.Row
	installments := make([]map[string]any, 0, len(render.Installments))
	for _, installment := range render.Installments {
		installments = append(installments, map[string]any{"course_subscription_installment_id": installment.ID, "id": installment.Number,
			"amount": installment.Amount, "payment_date": installment.PaymentDate.UTC().Format("2006-01-02"), "paid": installment.Paid})
	}
	payments := make([]map[string]any, 0, len(render.Payments))
	for _, payment := range render.Payments {
		item, err := PaymentStateItem(domain.PaymentStateRender{Payment: payment, Instructor: payment.Instructor, Invoice: payment.Invoice})
		if err != nil {
			return nil, err
		}
		payments = append(payments, item)
	}
	carnets := make([]map[string]any, 0, len(render.Carnets))
	for _, carnet := range render.Carnets {
		item, err := courseSubscriptionCarnet(carnet)
		if err != nil {
			return nil, err
		}
		carnets = append(carnets, item)
	}
	var subscription, label any
	if row.SubscriptionID != nil {
		person := render.Person
		if person == nil || person.Associate == nil {
			// get_medical_label() dereferences the associate.
			return nil, ErrCourseSubscriptionRender
		}
		associate := InconsistencyAssociateFrom(*person.Associate)
		subscription = map[string]any{"subscription_id": person.ID, "associate": associate,
			"current_year": SubscriptionCurrentYear(person.StartDate, person.EndDate, now.UTC())}
		label = CourseSubscriptionMedicalLabel(*person, now)
	}
	quote := courseSubscriptionJSON(row.MultipleQuote)
	if render.QuoteSet {
		quote = json.RawMessage(PythonJSONDumps(render.Quote))
	}
	return map[string]any{"course_subscription_id": row.ID, "creation_date": DRFUTCDateTime(row.CreationDate), "paid": row.Paid,
		"subscription": subscription, "multi_payments": row.MultiPayments, "multiple_quote": quote, "one_fee_payment": row.OneFeePayment,
		"installments": installments, "course": row.CourseID, "type": row.Type, "billed_frequency": row.BilledFrequency,
		"billed_from": courseSubscriptionTime(row.BilledFrom), "billed_until": courseSubscriptionTime(row.BilledUntil),
		"billed_from_day_of_month": row.BilledFromDayOfMonth, "membership_active": row.MembershipActive, "membership_fee": row.MembershipFee,
		"auto_renewal": row.AutoRenewal, "membership_payments": payments, "medical_label": label, "carnets": carnets}, nil
}

// CourseSubscriptionMemory names the instance attributes the serializer set
// (their Python types differ from values read from the database).
type CourseSubscriptionMemory struct {
	Times    map[string]bool
	QuoteSet bool
	Quote    any
}

func pythonDatetimeRepr(value time.Time, zoneinfo bool) string {
	value = value.UTC()
	parts := []int{value.Year(), int(value.Month()), value.Day(), value.Hour(), value.Minute(), value.Second(), value.Nanosecond() / 1000}
	if parts[6] == 0 {
		parts = parts[:6]
		if parts[5] == 0 {
			parts = parts[:5]
		}
	}
	text := make([]string, len(parts))
	for index, part := range parts {
		text[index] = strconv.Itoa(part)
	}
	zone := "datetime.timezone.utc"
	if zoneinfo {
		zone = "zoneinfo.ZoneInfo(key='UTC')"
	}
	return "datetime.datetime(" + strings.Join(text, ", ") + ", tzinfo=" + zone + ")"
}

func pythonValueRepr(value any) string {
	if text, ok := value.(string); ok {
		return PythonStringRepr(text)
	}
	return PythonStr(value)
}

// CourseSubscriptionRepr is auditlog's object_repr: CourseSubscription.__str__
// is str(self.__dict__). The ModelState address is not reproducible.
func CourseSubscriptionRepr(row domain.CourseSubscriptionRow, memory CourseSubscriptionMemory) string {
	identifier := func(value *string) string {
		if value == nil {
			return "None"
		}
		return "UUID('" + *value + "')"
	}
	moment := func(name string, value *time.Time) string {
		if value == nil {
			return "None"
		}
		return pythonDatetimeRepr(*value, memory.Times[name])
	}
	flag := func(value bool) string { return map[bool]string{true: "True", false: "False"}[value] }
	quote := "None"
	if memory.QuoteSet {
		quote = pythonValueRepr(memory.Quote)
	} else if row.MultipleQuote != nil {
		if parsed, err := ParseReferenceJSON([]byte(*row.MultipleQuote)); err == nil {
			quote = pythonValueRepr(parsed)
		}
	}
	fee := "None"
	if row.MembershipFee != nil {
		fee = "Decimal('" + *row.MembershipFee + "')"
	}
	created := row.CreationDate
	fields := [][2]string{
		{"course_subscription_id", identifier(&row.ID)}, {"course_id", identifier(&row.CourseID)}, {"paid", flag(row.Paid)},
		{"creation_date", moment("creation_date", &created)}, {"subscription_id", identifier(row.SubscriptionID)},
		{"payment_id", identifier(row.PaymentID)}, {"multi_payments", flag(row.MultiPayments)}, {"one_fee_payment", flag(row.OneFeePayment)},
		{"archived", flag(row.Archived)}, {"type", strconv.Itoa(row.Type)}, {"multiple_quote", quote},
		{"billed_frequency", strconv.Itoa(row.BilledFrequency)}, {"billed_from", moment("billed_from", row.BilledFrom)},
		{"billed_until", moment("billed_until", row.BilledUntil)}, {"billed_from_day_of_month", strconv.Itoa(row.BilledFromDayOfMonth)},
		{"membership_active", flag(row.MembershipActive)}, {"membership_fee", fee}, {"auto_renewal", flag(row.AutoRenewal)},
		{"deleted", flag(row.Deleted)},
	}
	var out strings.Builder
	out.WriteString("{'_state': <django.db.models.base.ModelState object at 0x7f0000000000>")
	for _, field := range fields {
		out.WriteString(", '" + field[0] + "': " + field[1])
	}
	out.WriteString("}")
	return out.String()
}

// CourseSubscriptionWrite is CourseSubscriptionOverviewSerializer's validated
// data (partial): only present fields are set.
type CourseSubscriptionWrite struct {
	ID             *string
	SubscriptionID *string
	CreationDate   *time.Time
	Bools          map[string]bool
	QuoteSet       bool
	Quote          any
	CourseSet      bool
	Course         string
	CourseRaw      string
	Ints           map[string]int
	Times          map[string]*time.Time
	FeeSet         bool
	Fee            *string
	Events         any
	// FormJSON marks form keys for the JSONFields (not ported).
	FormJSON bool
}

// CourseSubscriptionBooleans are the serializer's BooleanFields.
var CourseSubscriptionBooleans = []string{"paid", "multi_payments", "one_fee_payment", "membership_active", "auto_renewal"}

var courseSubscriptionIntegers = []struct {
	name     string
	min, max int64
}{{"billed_frequency", 0, 32767}, {"billed_from_day_of_month", 1, 28}}

// CourseSubscriptionUnique is the UniqueValidator code of the primary key.
const CourseSubscriptionUnique = "unique:course subscription:course subscription id"

// courseSubscriptionUUID is serializers.UUIDField.to_internal_value (a bool is
// an int, UUID(int=...)).
func courseSubscriptionUUID(value any) (string, bool) {
	if flag, ok := value.(bool); ok {
		if flag {
			return "00000000-0000-0000-0000-000000000001", true
		}
		return "00000000-0000-0000-0000-000000000000", true
	}
	return supplierUUID(value)
}

func courseSubscriptionDateTime(value any) (*time.Time, string) {
	text, ok := value.(string)
	if !ok {
		return nil, PaymentCategoryValidationDateTime
	}
	parsed, err := parsePaymentCategoryDate(text)
	if err != nil {
		if errors.Is(err, errPaymentCategoryDateTimeOverflow) {
			return nil, PaymentCategoryValidationDateTimeOverflow
		}
		return nil, PaymentCategoryValidationDateTime
	}
	parsed = parsed.UTC()
	return &parsed, ""
}

// ParseCourseSubscriptionWrite ports the serializer's field validation for a
// partial update. The course lookup, uniqueness and validate() are the
// service's.
func ParseCourseSubscriptionWrite(body any, form bool) (CourseSubscriptionWrite, map[string]any) {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return CourseSubscriptionWrite{}, map[string]any{"non_field_errors": []string{"dictionary:" + pythonTypeName(body)}}
	}
	write := CourseSubscriptionWrite{Bools: map[string]bool{}, Ints: map[string]int{}, Times: map[string]*time.Time{}}
	errs := map[string]any{}
	// get_value: an empty HTML value of a non-null optional field is skipped.
	skip := func(value any) bool { return form && value == "" }
	if value, present := object.Lookup("course_subscription_id"); present && !skip(value) {
		if value == nil {
			errs["course_subscription_id"] = []string{PaymentCategoryValidationNull}
		} else if id, ok := courseSubscriptionUUID(value); ok {
			write.ID = &id
		} else {
			errs["course_subscription_id"] = []string{PaymentCategoryValidationUUID}
		}
	}
	// subscription_id is declared required: an empty HTML value is validated.
	if value, present := object.Lookup("subscription_id"); present {
		if value == nil {
			errs["subscription_id"] = []string{PaymentCategoryValidationNull}
		} else if id, ok := courseSubscriptionUUID(value); ok {
			write.SubscriptionID = &id
		} else {
			errs["subscription_id"] = []string{PaymentCategoryValidationUUID}
		}
	}
	if value, present := object.Lookup("creation_date"); present && !skip(value) {
		if value == nil {
			errs["creation_date"] = []string{PaymentCategoryValidationNull}
		} else if parsed, code := courseSubscriptionDateTime(value); code != "" {
			errs["creation_date"] = []string{code}
		} else {
			write.CreationDate = parsed
		}
	}
	for _, name := range CourseSubscriptionBooleans {
		value, present := object.Lookup(name)
		if !present || skip(value) {
			continue
		}
		if value == nil {
			errs[name] = []string{PaymentCategoryValidationNull}
		} else if flag, err := parsePaymentCategoryBoolean(value); err != nil {
			errs[name] = []string{PaymentCategoryValidationBoolean}
		} else {
			write.Bools[name] = flag
		}
	}
	for _, name := range []string{"multiple_quote", "events"} {
		value, present := object.Lookup(name)
		if !present {
			continue
		}
		if form {
			write.FormJSON = true
			continue
		}
		if name == "events" {
			write.Events = value
		} else {
			write.QuoteSet, write.Quote = true, value
		}
	}
	// RelatedField.run_validation: '' is None, which the field rejects.
	if value, present := object.Lookup("course"); present {
		if value == nil || value == "" {
			errs["course"] = []string{PaymentCategoryValidationNull}
		} else if id, code := supplierRelated(value); code != "" {
			errs["course"] = []string{code}
		} else {
			write.CourseSet, write.Course, write.CourseRaw = true, id, PythonStr(value)
		}
	}
	for _, field := range courseSubscriptionIntegers {
		value, present := object.Lookup(field.name)
		if !present || skip(value) {
			continue
		}
		if value == nil {
			errs[field.name] = []string{PaymentCategoryValidationNull}
			continue
		}
		number, code := DRFInteger(value)
		switch {
		case code != "":
			errs[field.name] = []string{code}
		case number.Cmp(big.NewInt(field.min)) < 0:
			errs[field.name] = []string{"min-value:" + strconv.FormatInt(field.min, 10)}
		case number.Cmp(big.NewInt(field.max)) > 0:
			errs[field.name] = []string{"max-value:" + strconv.FormatInt(field.max, 10)}
		default:
			write.Ints[field.name] = int(number.Int64())
		}
	}
	for _, name := range []string{"billed_from", "billed_until"} {
		value, present := object.Lookup(name)
		if !present {
			continue
		}
		if value == nil || form && value == "" {
			write.Times[name] = nil
		} else if parsed, code := courseSubscriptionDateTime(value); code != "" {
			errs[name] = []string{code}
		} else {
			write.Times[name] = parsed
		}
	}
	if value, present := object.Lookup("membership_fee"); present {
		// DecimalField.validate_empty_values: a blank allow_null decimal is None.
		if value == nil || PythonStrip(PythonStr(value)) == "" {
			write.FeeSet = true
		} else if fee, code := DRFDecimal(value, 9, 2); code != "" {
			errs["membership_fee"] = []string{code}
		} else {
			write.FeeSet, write.Fee = true, &fee
		}
	}
	if len(errs) > 0 {
		return write, errs
	}
	return write, nil
}

// courseSubscriptionYMD is _strptime's regex for '%Y-%m-%d' (\d matches any
// Unicode decimal digit; the whole input must be consumed).
var courseSubscriptionYMD = regexp.MustCompile(`^(\p{Nd}{4})-(1[0-2]|0[1-9]|[1-9])-(3[01]|[12]\p{Nd}|0[1-9]|[1-9]| [1-9])$`)

// CourseSubscriptionStrptime is datetime.strptime(value, '%Y-%m-%d') of a
// meta value (a non-string is a TypeError).
func CourseSubscriptionStrptime(value any) (time.Time, bool) {
	text, ok := value.(string)
	if !ok {
		return time.Time{}, false
	}
	match := courseSubscriptionYMD.FindStringSubmatch(text)
	if match == nil {
		return time.Time{}, false
	}
	number := func(digits string) int {
		result := 0
		for _, r := range strings.TrimSpace(digits) {
			digit, _ := pythonDigit(r)
			result = result*10 + digit
		}
		return result
	}
	year, month, day := number(match[1]), number(match[2]), number(match[3])
	if year < 1 {
		return time.Time{}, false
	}
	date := time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC)
	if date.Day() != day || int(date.Month()) != month {
		return time.Time{}, false
	}
	return date, true
}

// CourseSubscriptionDecimalNegative is Decimal(text) < 0.
func CourseSubscriptionDecimalNegative(text string) bool {
	value, ok := new(big.Rat).SetString(text)
	return ok && value.Sign() < 0
}
