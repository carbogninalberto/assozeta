package importeffects

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// Payment subjects (BE/application/models/payment_models.py).
const (
	paymentOther        = "0"
	paymentSubscription = "1"
	paymentCourse       = "2"
	paymentTransfer     = "3"
)

// paymentSave is Payment.save() for a new payment: the default category, the
// description get_course_carnet_name derives, the quota-corso meta override and
// the default bank account. No row references the new payment yet (its primary
// key passed the collision check and foreign keys are enforced), so the three
// CourseSubscription/CarnetSubscription lookups find nothing.
func (e *Effects) paymentSave(ctx context.Context, instance *Instance) error {
	categoryName := sql.NullString{}
	category := instance.Get("payment_category_id")
	if category.IsNone() {
		// PaymentCategory.default_for(self.sport_association_id): the association's own or a global row.
		var association *string
		switch value := instance.Get("sport_association_id"); value.Kind {
		case None:
		case UUID:
			association = &value.S
		default:
			// ParseRelated resolves a UUID foreign key to uuid.UUID or None.
			return planUnreachable("payment association kind %d", value.Kind)
		}
		var row struct {
			ID   string         `db:"payment_category_id"`
			Name sql.NullString `db:"name"`
		}
		err := e.tx().GetContext(ctx, &row, `SELECT "payment_category_id"::text, "name" FROM "application_paymentcategory"
 WHERE UPPER("name"::text) = UPPER($1) AND ("sport_association_id" IS NULL OR "sport_association_id" = $2::uuid)
 ORDER BY "payment_category_id" ASC LIMIT 1`, "entrate e proventi da attività tipiche", association)
		switch {
		case errors.Is(err, sql.ErrNoRows):
		case err != nil:
			return err
		default:
			instance.set("payment_category_id", UUIDValue(row.ID))
			categoryName = row.Name
		}
	} else {
		// `self.payment_category` loads the related row.
		bound, err := dbValue(instance.Model.FieldByAttname("payment_category_id"), category)
		if err != nil {
			return err
		}
		err = e.tx().GetContext(ctx, &categoryName, `SELECT "name" FROM "application_paymentcategory" WHERE "payment_category_id" = $1::uuid`,
			bound)
		if errors.Is(err, sql.ErrNoRows) {
			return djangoError("Payment has no payment_category.")
		}
		if err != nil {
			return err
		}
	}
	description := instance.Get("description")
	if description.IsNone() {
		computed, err := e.paymentCourseName(ctx, instance, categoryName)
		if err != nil {
			return err
		}
		if computed.IsNone() && paymentSubject(instance) == paymentCourse {
			computed = StrValue("Quota Corso")
		}
		description = computed
		instance.set("description", description)
	}
	if pyTruthy(description) {
		lower, err := pyLower(description)
		if err != nil {
			return err
		}
		if lower == "quota corso" {
			meta := instance.Get("meta")
			if pyTruthy(meta) {
				contains, err := pyContains(meta, "description")
				if err != nil {
					return err
				}
				if contains {
					value, err := pyIndex(meta, "description")
					if err != nil {
						return err
					}
					instance.set("description", value)
				}
			}
		}
	}
	if instance.PK().IsNone() {
		if !instance.Get("user_id").IsNone() {
			// `self.user` loads the related row (and raises when it is missing).
			var exists bool
			bound, err := dbValue(instance.Model.FieldByAttname("user_id"), instance.Get("user_id"))
			if err != nil {
				return err
			}
			if err := e.tx().GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM "bakney_user" WHERE "user_id" = $1::uuid)`, bound); err != nil {
				return err
			}
			if !exists {
				return djangoError("Payment has no user.")
			}
			if instance.Get("custom_accounts_id").IsNone() {
				account, err := e.defaultBankAccount(ctx, instance)
				if err != nil {
					return err
				}
				instance.set("custom_accounts_id", account)
			}
		}
	}
	return nil
}

// paymentSubject is self.subject (an int or None).
func paymentSubject(instance *Instance) string {
	subject := instance.Get("subject")
	if subject.Kind != Int {
		return ""
	}
	return subject.S
}

// paymentCourseName is get_course_carnet_name.
func (e *Effects) paymentCourseName(ctx context.Context, instance *Instance, categoryName sql.NullString) (Value, error) {
	switch paymentSubject(instance) {
	case paymentCourse:
		if instance.PK().IsNone() {
			// CourseSubscription.objects.all_objects().filter(payment=self)
			return NoneValue, djangoError("Model instances passed to related filters must be saved.")
		}
		course := instance.Get("course")
		if pyTruthy(course) {
			contains, err := pyContains(course, "label")
			if err != nil {
				return Value{}, err
			}
			if contains {
				label, err := pyIndex(course, "label")
				if err != nil {
					return Value{}, err
				}
				second := StrValue("")
				if pyTruthy(instance.Get("description")) {
					second = instance.Get("description")
				}
				joined, err := pyJoin([]Value{label, second})
				if err != nil {
					return Value{}, err
				}
				return StrValue(joined), nil
			}
		}
		return instance.Get("description"), nil
	case paymentSubscription:
		return StrValue("Iscrizione"), nil
	case paymentOther:
		if instance.Get("payment_category_id").IsNone() {
			return NoneValue, nil
		}
		if !categoryName.Valid {
			return NoneValue, nil
		}
		return StrValue(categoryName.String), nil
	case paymentTransfer:
		accounts := instance.Get("custom_accounts_id")
		if accounts.IsNone() {
			return Value{}, djangoError("'NoneType' object has no attribute 'name'")
		}
		bound, err := dbValue(instance.Model.FieldByAttname("custom_accounts_id"), accounts)
		if err != nil {
			return Value{}, err
		}
		var row struct {
			Name sql.NullString `db:"name"`
		}
		err = e.tx().GetContext(ctx, &row, `SELECT "name" FROM "application_customaccounts" WHERE "custom_account_id" = $1::uuid`, bound)
		if errors.Is(err, sql.ErrNoRows) {
			return Value{}, djangoError("Payment has no custom_accounts.")
		}
		if err != nil {
			return Value{}, err
		}
		// CustomAccounts has no destination_account_name: Python's f-string
		// reads it and raises.
		return Value{}, djangoError("'CustomAccounts' object has no attribute 'destination_account_name'")
	}
	return NoneValue, nil
}

// defaultBankAccount is Payment.get_default_bank_account(self.sport_association).
func (e *Effects) defaultBankAccount(ctx context.Context, instance *Instance) (Value, error) {
	association := instance.Get("sport_association_id")
	if association.IsNone() {
		return NoneValue, nil
	}
	bound, err := dbValue(instance.Model.FieldByAttname("sport_association_id"), association)
	if err != nil {
		return Value{}, err
	}
	var id string
	err = e.tx().GetContext(ctx, &id, `SELECT "custom_account_id"::text FROM "application_customaccounts"
 WHERE "sport_association_id" = $1::uuid AND "editable" = false AND "enabled" = true AND "account_type" = 2 LIMIT 1`, bound)
	if errors.Is(err, sql.ErrNoRows) {
		return NoneValue, nil
	}
	if err != nil {
		return Value{}, err
	}
	return UUIDValue(id), nil
}

// pyLower is value.lower().
func pyLower(v Value) (string, error) {
	if v.Kind != Str {
		return "", djangoError(fmt.Sprintf("'%s' object has no attribute 'lower'", pyTypeName(v)))
	}
	return dto.PythonLower(v.S), nil
}

// pyContains is item in value.
func pyContains(value Value, item string) (bool, error) {
	switch value.Kind {
	case Str:
		return strings.Contains(value.S, item), nil
	case JSON:
		switch container := value.J.(type) {
		case domain.RequestObject:
			for _, field := range container {
				if field.Name == item {
					return true, nil
				}
			}
			return false, nil
		case []any:
			for _, element := range container {
				if text, ok := element.(string); ok && text == item {
					return true, nil
				}
			}
			return false, nil
		}
	}
	return false, djangoError(fmt.Sprintf("argument of type '%s' is not iterable", pyTypeName(value)))
}

// pyIndex is value[key].
func pyIndex(value Value, key string) (Value, error) {
	switch value.Kind {
	case JSON:
		switch container := value.J.(type) {
		case domain.RequestObject:
			var found any
			present := false
			for _, field := range container {
				if field.Name == key {
					found, present = field.Value, true
				}
			}
			if !present {
				return Value{}, djangoError(dto.PythonStringRepr(key))
			}
			return rawValue(found)
		case []any:
			return Value{}, djangoError("list indices must be integers or slices, not str")
		}
	case Str:
		return Value{}, djangoError("string indices must be integers, not 'str'")
	}
	return Value{}, djangoError(fmt.Sprintf("'%s' object is not subscriptable", pyTypeName(value)))
}

// pyJoin is ", ".join(items).
func pyJoin(items []Value) (string, error) {
	texts := make([]string, len(items))
	for i, item := range items {
		if item.Kind != Str {
			return "", djangoError(fmt.Sprintf("sequence item %d: expected str instance, %s found", i, pyTypeName(item)))
		}
		texts[i] = item.S
	}
	return strings.Join(texts, ", "), nil
}

// subscriptionSave is Subscription.save(): the season range computation, then
// the dates the association owner's settings fill in.
func (e *Effects) subscriptionSave(ctx context.Context, instance *Instance) error {
	created := instance.Get("creation_date")
	if created.Kind == Str {
		// isinstance(self.creation_date, str): parse_date + datetime.combine.
		date, err := parseDate(created.S)
		if err != nil {
			return err
		}
		if date == nil {
			return typeError("combine() argument 1 must be datetime.date, not NoneType")
		}
		created = Value{Kind: DateTime, T: time.Date(date.year, time.Month(date.month), date.day, 0, 0, 0, 0, time.UTC), Naive: true}
		instance.set("creation_date", created)
	}
	owner, err := e.subscriptionOwner(ctx, instance)
	if err != nil {
		return err
	}
	from, to, err := e.seasonRange(created, owner)
	if err != nil {
		return err
	}
	if !pyTruthy(instance.Get("start_date")) {
		switch owner.duration {
		case 1: // User.FULL_YEAR
			instance.set("start_date", dateOf(created))
		case 2: // User.LIKE_SEASON_YEAR
			instance.set("start_date", Value{Kind: Date, S: from.Format("2006-01-02")})
		case 3: // User.LIKE_SEASON_YEAR_FROM_TIME
			instance.set("start_date", dateOf(created))
		}
	}
	if !pyTruthy(instance.Get("end_date")) {
		switch owner.duration {
		case 1:
			instance.set("end_date", addYears(dateOf(created), 1))
		case 2, 3:
			instance.set("end_date", Value{Kind: Date, S: to.Format("2006-01-02")})
		}
	}
	return nil
}

// dateOf is creation_date.date().
func dateOf(created Value) Value {
	if created.Kind != DateTime {
		return Value{}
	}
	return Value{Kind: Date, S: created.T.Format("2006-01-02")}
}

// addYears is date + relativedelta(years=1): the day is clamped to the month.
func addYears(date Value, years int) Value {
	moment, _ := time.Parse("2006-01-02", date.S)
	year, month, day := moment.Year()+years, moment.Month(), moment.Day()
	if last := daysInMonth(year, int(month)); day > last {
		day = last
	}
	return Value{Kind: Date, S: fmt.Sprintf("%04d-%02d-%02d", year, int(month), day)}
}

// subscriptionOwner is self.sport_association.user and the settings save() reads.
type subscriptionOwner struct {
	duration  int64
	startDay  int64
	startMon  int64
	customEnd bool
	endMonth  int64
	endDay    int64
}

func (e *Effects) subscriptionOwner(ctx context.Context, instance *Instance) (subscriptionOwner, error) {
	association := instance.Get("sport_association_id")
	if association.IsNone() {
		return subscriptionOwner{}, djangoError("'NoneType' object has no attribute 'user'")
	}
	bound, err := dbValue(instance.Model.FieldByAttname("sport_association_id"), association)
	if err != nil {
		return subscriptionOwner{}, err
	}
	var userID sql.NullString
	err = e.tx().GetContext(ctx, &userID, `SELECT "user_id"::text FROM "application_sportassociation"
 WHERE "sport_association_id" = $1::uuid`, bound)
	if errors.Is(err, sql.ErrNoRows) {
		return subscriptionOwner{}, djangoError("Subscription has no sport_association.")
	}
	if err != nil {
		return subscriptionOwner{}, err
	}
	if !userID.Valid {
		return subscriptionOwner{}, djangoError("'NoneType' object has no attribute 'subscription_start_day'")
	}
	var owner subscriptionOwner
	err = e.tx().GetContext(ctx, &owner, `SELECT "subscription_duration", "subscription_start_day", "subscription_start_month",
 "custom_end_date", "subscription_end_month", "subscription_end_day" FROM "bakney_user" WHERE "user_id" = $1::uuid`, userID.String)
	if errors.Is(err, sql.ErrNoRows) {
		return subscriptionOwner{}, djangoError("SportAssociation has no user.")
	}
	if err != nil {
		return subscriptionOwner{}, err
	}
	return owner, nil
}

// seasonRange is BalanceSheetData.get_range_from_year_and_starting_date with
// the owner, in the default timezone (UTC).
func (e *Effects) seasonRange(date Value, owner subscriptionOwner) (time.Time, time.Time, error) {
	// get_range_from_year_and_starting_date validates the settings before it
	// reads date.year.
	if owner.startMon < 1 || owner.startMon > 12 {
		return time.Time{}, time.Time{}, djangoError("Month must be between 1 and 12")
	}
	if owner.startDay < 1 || owner.startDay > 31 {
		return time.Time{}, time.Time{}, djangoError("Day must be between 1 and 31")
	}
	if date.Kind != DateTime {
		return time.Time{}, time.Time{}, djangoError(fmt.Sprintf("'%s' object has no attribute 'year'", pyTypeName(date)))
	}
	year := date.T.Year()
	current, err := newPyDate(year, int(owner.startMon), int(owner.startDay))
	if err != nil {
		return time.Time{}, time.Time{}, djangoError("Invalid date combination: " + err.Error())
	}
	previous, err := newPyDate(year-1, int(owner.startMon), int(owner.startDay))
	if err != nil {
		return time.Time{}, time.Time{}, djangoError("Invalid date combination: " + err.Error())
	}
	currentStart := time.Date(current.year, time.Month(current.month), current.day, 0, 0, 0, 0, time.UTC)
	previousStart := time.Date(previous.year, time.Month(previous.month), previous.day, 0, 0, 0, 0, time.UTC)
	value := date.T
	if date.Naive {
		value = time.Date(value.Year(), value.Month(), value.Day(), value.Hour(), value.Minute(), value.Second(), value.Nanosecond(), time.UTC)
	}
	start := previousStart
	if !value.Before(currentStart) {
		start = currentStart
	}
	if owner.customEnd && owner.endMonth != 0 && owner.endDay != 0 {
		end, err := newPyDate(start.Year(), int(owner.endMonth), int(owner.endDay))
		if err != nil {
			return time.Time{}, time.Time{}, err
		}
		endMoment := time.Date(end.year, time.Month(end.month), end.day, 0, 0, 0, 0, time.UTC)
		if endMoment.Before(start) {
			next, err := newPyDate(start.Year()+1, int(owner.endMonth), int(owner.endDay))
			if err != nil {
				return time.Time{}, time.Time{}, err
			}
			endMoment = time.Date(next.year, time.Month(next.month), next.day, 0, 0, 0, 0, time.UTC)
		}
		return start, endMoment, nil
	}
	next, err := newPyDate(start.Year()+1, int(owner.startMon), int(owner.startDay))
	if err != nil {
		return time.Time{}, time.Time{}, err
	}
	return start, time.Date(next.year, time.Month(next.month), next.day, 0, 0, 0, 0, time.UTC).AddDate(0, 0, -1), nil
}

// newPyDate is datetime.date(year, month, day).
func newPyDate(year, month, day int) (pyDate, error) {
	date := pyDate{year: year, month: month, day: day}
	if err := validatePyDate(date); err != nil {
		return pyDate{}, err
	}
	return date, nil
}
