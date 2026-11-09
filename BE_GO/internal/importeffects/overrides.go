package importeffects

import (
	"context"
	"database/sql"
	"errors"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// Payment subjects (BE/application/models/payment_models.py).
const (
	paymentOther        = "0"
	paymentSubscription = "1"
	paymentCourse       = "2"
	paymentTransfer     = "3"
)

// paymentSave is Payment.save() for a new payment whose primary key is set.
// No row references the new payment yet, so get_course_carnet_name's
// CourseSubscription, CourseSubscriptionInstallment and CarnetSubscription
// lookups find nothing.
func (e *Effects) paymentSave(ctx context.Context, instance *Instance) error {
	if instance.PK().IsNone() {
		return unsupported("payment without a key")
	}
	categoryName := sql.NullString{}
	category := instance.Get("payment_category_id")
	if category.IsNone() {
		// PaymentCategory.objects.filter(name__iexact=...).first()
		var row struct {
			ID   string         `db:"payment_category_id"`
			Name sql.NullString `db:"name"`
		}
		err := e.tx().GetContext(ctx, &row, `SELECT "payment_category_id"::text, "name" FROM "application_paymentcategory"
 WHERE UPPER("name"::text) = UPPER($1) ORDER BY "payment_category_id" ASC LIMIT 1`, "entrate e proventi da attività tipiche")
		switch {
		case errors.Is(err, sql.ErrNoRows):
		case err != nil:
			return err
		default:
			instance.set("payment_category_id", UUIDValue(row.ID))
			categoryName = row.Name
		}
	} else {
		// `self.payment_category is None` loads the related row through the base manager.
		err := e.tx().GetContext(ctx, &categoryName, `SELECT "name" FROM "application_paymentcategory" WHERE "payment_category_id" = $1::uuid`,
			category.S)
		if errors.Is(err, sql.ErrNoRows) {
			return unsupported("payment category reference without a row")
		}
		if err != nil {
			return err
		}
	}
	subject := instance.Get("subject")
	description := instance.Get("description")
	if description.IsNone() {
		subjectText := ""
		if subject.Kind == Int {
			subjectText = subject.S
		} else if !subject.IsNone() {
			return unsupported("payment subject")
		}
		switch subjectText {
		case paymentCourse:
			course := instance.Get("course")
			if course.Kind == JSON {
				object, ok := course.J.(domain.RequestObject)
				if !ok {
					return unsupported("payment course value")
				}
				for _, item := range object {
					if item.Name == "label" {
						label, ok := item.Value.(string)
						if !ok {
							return unsupported("payment course label")
						}
						description = StrValue(label + ", ")
					}
				}
			} else if !course.IsNone() {
				return unsupported("payment course value")
			}
		case paymentSubscription:
			description = StrValue("Iscrizione")
		case paymentOther:
			if instance.Get("payment_category_id").IsNone() {
				description = NoneValue
			} else if categoryName.Valid {
				description = StrValue(categoryName.String)
			} else {
				return unsupported("payment category without a name")
			}
		case paymentTransfer:
			return unsupported("account transfer description")
		}
		if description.IsNone() && subjectText == paymentCourse {
			description = StrValue("Quota Corso")
		}
		instance.set("description", description)
	}
	if description.Kind != Str && !description.IsNone() {
		return unsupported("payment description value")
	}
	if description.Kind == Str && description.S != "" && strings.ToLower(description.S) == "quota corso" {
		meta := instance.Get("meta")
		if meta.Kind == JSON {
			object, ok := meta.J.(domain.RequestObject)
			if !ok {
				return unsupported("payment meta value")
			}
			for _, item := range object {
				if item.Name == "description" {
					text, ok := item.Value.(string)
					if !ok {
						return unsupported("payment meta description")
					}
					instance.set("description", StrValue(text))
				}
			}
		} else if !meta.IsNone() {
			return unsupported("payment meta value")
		}
	}
	return nil
}

// subscriptionSave is Subscription.save(): with start_date and end_date set,
// only BalanceSheetData.get_range_from_year_and_starting_date runs (its result
// is unused), reading the association owner's season settings through the
// related descriptors. Inputs that make it raise, or that would fill the
// dates, are unsupported.
func (e *Effects) subscriptionSave(ctx context.Context, instance *Instance) error {
	created := instance.Get("creation_date")
	association := instance.Get("sport_association_id")
	if created.Kind != DateTime || association.Kind != UUID || instance.Get("start_date").Kind != Date ||
		instance.Get("end_date").Kind != Date {
		return unsupported("subscription dates")
	}
	var owner struct {
		StartDay   int  `db:"subscription_start_day"`
		StartMonth int  `db:"subscription_start_month"`
		CustomEnd  bool `db:"custom_end_date"`
		EndMonth   int  `db:"subscription_end_month"`
		EndDay     int  `db:"subscription_end_day"`
	}
	err := e.tx().GetContext(ctx, &owner, `SELECT u."subscription_start_day", u."subscription_start_month", u."custom_end_date",
 u."subscription_end_month", u."subscription_end_day" FROM "application_sportassociation" sa
 JOIN "bakney_user" u ON u."user_id" = sa."user_id" WHERE sa."sport_association_id" = $1::uuid`, association.S)
	if errors.Is(err, sql.ErrNoRows) {
		return unsupported("subscription association")
	}
	if err != nil {
		return err
	}
	if !seasonRangeValid(created.T, owner.StartDay, owner.StartMonth, owner.CustomEnd, owner.EndMonth, owner.EndDay) {
		return unsupported("season settings")
	}
	return nil
}

func validDate(year, month, day int) bool {
	if year < 1 || year > 9999 || month < 1 || month > 12 || day < 1 {
		return false
	}
	return time.Date(year, time.Month(month), day, 0, 0, 0, 0, time.UTC).Day() == day
}

// seasonRangeValid reports whether get_range_from_year_and_starting_date
// returns for these inputs (TIME_ZONE is UTC).
func seasonRangeValid(date time.Time, startDay, startMonth int, customEnd bool, endMonth, endDay int) bool {
	if startMonth < 1 || startMonth > 12 || startDay < 1 || startDay > 31 {
		return false
	}
	year := date.Year()
	if !validDate(year, startMonth, startDay) || !validDate(year-1, startMonth, startDay) {
		return false
	}
	current := time.Date(year, time.Month(startMonth), startDay, 0, 0, 0, 0, time.UTC)
	start := time.Date(year-1, time.Month(startMonth), startDay, 0, 0, 0, 0, time.UTC)
	if !date.Before(current) {
		start = current
	}
	if customEnd && endMonth != 0 && endDay != 0 {
		if !validDate(start.Year(), endMonth, endDay) {
			return false
		}
		end := time.Date(start.Year(), time.Month(endMonth), endDay, 0, 0, 0, 0, time.UTC)
		if end.Before(start) && !validDate(start.Year()+1, endMonth, endDay) {
			return false
		}
		return true
	}
	return validDate(start.Year()+1, startMonth, startDay)
}
