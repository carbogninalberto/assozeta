package dto

import (
	"encoding/json"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// CarnetListItem is CarnetListSerializer in declared field order.
type CarnetListItem struct {
	ID            string          `json:"carnet_id"`
	Title         *string         `json:"title"`
	Description   *string         `json:"description"`
	LessonsNumber int             `json:"lessons_number"`
	CreationDate  string          `json:"creation_date"`
	Fee           string          `json:"fee"`
	Public        bool            `json:"public"`
	Meta          json.RawMessage `json:"meta"`
}

type CarnetHoldingCarnet struct {
	ID    string  `json:"carnet_id"`
	Title *string `json:"title"`
}

// CarnetHoldingSubscription is SubscriptionSerializerSimplify.
type CarnetHoldingSubscription struct {
	ID          string                  `json:"subscription_id"`
	Associate   *InconsistencyAssociate `json:"associate"`
	CurrentYear string                  `json:"current_year"`
}

type CarnetHoldingCourse struct {
	CourseID             string  `json:"course_id"`
	CourseSubscriptionID string  `json:"course_subscription_id"`
	CourseTitle          *string `json:"course_title"`
}

// CarnetHolding is CarnetSubscriptionSerializer in declared field order.
type CarnetHolding struct {
	ID                 string                     `json:"carnet_subscription_id"`
	UserID             *string                    `json:"user_id"`
	Carnet             CarnetHoldingCarnet        `json:"carnet"`
	Subscription       *CarnetHoldingSubscription `json:"subscription"`
	CourseSubscription []string                   `json:"course_subscription"`
	Course             []CarnetHoldingCourse      `json:"course"`
	CarnetID           string                     `json:"carnet_id"`
	CreationDate       string                     `json:"creation_date"`
	Payment            *string                    `json:"payment"`
	Meta               json.RawMessage            `json:"meta"`
}

func rawJSON(text *string) json.RawMessage {
	if text == nil {
		return nil
	}
	return json.RawMessage(*text)
}

func CarnetListItemFrom(row domain.CarnetListCarnet) CarnetListItem {
	return CarnetListItem{ID: row.ID, Title: row.Title, Description: row.Description, LessonsNumber: row.LessonsNumber,
		CreationDate: DRFUTCDateTime(row.CreationDate), Fee: row.Fee, Public: row.Public, Meta: rawJSON(row.Meta)}
}

// SubscriptionCurrentYear is Subscription.current_year() for a UTC date.
func SubscriptionCurrentYear(start, end *time.Time, today time.Time) string {
	if start == nil || end == nil {
		return "corrente"
	}
	day := func(value time.Time) string { return value.Format("2006-01-02") }
	switch now := day(today); {
	case day(*start) <= now && now <= day(*end):
		return "corrente"
	case day(*end) < now:
		return "precedente"
	default:
		return "successivo"
	}
}

// CarnetHoldingFrom requires the carnet: the serializer dereferences it.
// The declared carnet_id UUIDField receives the Carnet instance, so it renders
// the model's str().
func CarnetHoldingFrom(row domain.CarnetListHolding, today time.Time) (CarnetHolding, error) {
	courses := []CarnetHoldingCourse{}
	if err := json.Unmarshal([]byte(row.CourseSubscriptions), &courses); err != nil {
		return CarnetHolding{}, err
	}
	links := make([]string, 0, len(courses))
	for _, course := range courses {
		links = append(links, course.CourseSubscriptionID)
	}
	holding := CarnetHolding{ID: row.ID, UserID: row.UserID, Carnet: CarnetHoldingCarnet{ID: *row.CarnetID, Title: row.CarnetTitle},
		CourseSubscription: links, Course: courses, CarnetID: "Carnet object (" + *row.CarnetID + ")",
		CreationDate: DRFUTCDateTime(row.CreationDate), Payment: row.PaymentID, Meta: rawJSON(row.Meta)}
	if row.SubscriptionID != nil {
		subscription := &CarnetHoldingSubscription{ID: *row.SubscriptionID, CurrentYear: SubscriptionCurrentYear(row.StartDate, row.EndDate, today)}
		if row.AssociateID != nil {
			associate := InconsistencyAssociateFrom(domain.InconsistencyAssociate{ID: *row.AssociateID, Address: row.Address,
				AddressCap: row.AddressCap, AddressCity: row.AddressCity, BornCity: row.BornCity, BornDate: row.BornDate, Email: row.Email,
				Sex: *row.Sex, FirstName: row.FirstName, LastName: row.LastName, IsMinor: *row.IsMinor, AssociationID: row.AssociationID,
				TaxCode: row.TaxCode, UserID: row.AssociateUserID, OwnerUserID: row.OwnerUserID, Phone: row.Phone, FamilyID: row.FamilyID,
				FamilyRole: row.FamilyRole})
			subscription.Associate = &associate
		}
		holding.Subscription = subscription
	}
	return holding, nil
}
