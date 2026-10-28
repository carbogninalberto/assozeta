package dto

import (
	"encoding/json"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type SearchProfileLocation struct {
	ID      string  `json:"course_location_id"`
	Title   *string `json:"title"`
	Address *string `json:"address"`
}

// SearchProfileCourse is CourseSerializer (fields '__all__', athletes, tags,
// locations) plus the view's total_subscriptions.
type SearchProfileCourse struct {
	ID                     string                    `json:"course_id"`
	AssociationID          *string                   `json:"sport_association"`
	StatusFlag             int                       `json:"status_flag"`
	CourseType             int                       `json:"course_type"`
	CreationDate           string                    `json:"creation_date"`
	Title                  *string                   `json:"title"`
	Description            *string                   `json:"description"`
	MultiPayments          bool                      `json:"multi_payments"`
	Fee                    *string                   `json:"fee"`
	Events                 json.RawMessage           `json:"events"`
	OneFeePayment          bool                      `json:"one_fee_payment"`
	OneFee                 string                    `json:"one_fee"`
	GoogleCalendarID       *string                   `json:"google_calendar_id"`
	GoogleBackgroundColor  *string                   `json:"google_background_color"`
	Tags                   []domain.SearchProfileTag `json:"tags"`
	MultipleQuotes         json.RawMessage           `json:"multiple_quotes"`
	Locations              []SearchProfileLocation   `json:"locations"`
	StartDate              *string                   `json:"start_date"`
	EndDate                *string                   `json:"end_date"`
	Pinned                 bool                      `json:"pinned"`
	BilledDurationIsSeason bool                      `json:"billed_duration_is_sport_season"`
	BilledFrequency        *int                      `json:"billed_frequency"`
	BilledFromSubscription bool                      `json:"billed_from_subscription_date"`
	BilledFromDayOfMonth   int                       `json:"billed_from_day_of_month"`
	AutoRenewal            bool                      `json:"auto_renewal"`
	Deleted                bool                      `json:"deleted"`
	GroupID                *string                   `json:"group"`
	// AthleteSerializer(allow_null=True) on a missing attribute renders null.
	Athletes           *struct{} `json:"athletes"`
	TotalSubscriptions int64     `json:"total_subscriptions"`
}

func SearchProfileCourseFrom(row domain.SearchProfileCourse) (SearchProfileCourse, error) {
	course := SearchProfileCourse{ID: row.ID, AssociationID: row.AssociationID, StatusFlag: row.StatusFlag, CourseType: row.CourseType,
		CreationDate: DRFUTCDateTime(row.CreationDate), Title: row.Title, Description: row.Description, MultiPayments: row.MultiPayments,
		Fee: row.Fee, Events: rawJSON(row.Events), OneFeePayment: row.OneFeePayment, OneFee: row.OneFee, GoogleCalendarID: row.GoogleCalendarID,
		GoogleBackgroundColor: row.GoogleBackgroundColor, MultipleQuotes: rawJSON(row.MultipleQuotes), Pinned: row.Pinned,
		BilledDurationIsSeason: row.BilledDurationIsSeason, BilledFrequency: row.BilledFrequency, BilledFromSubscription: row.BilledFromSubscription,
		BilledFromDayOfMonth: row.BilledFromDayOfMonth, AutoRenewal: row.AutoRenewal, Deleted: row.Deleted, GroupID: row.GroupID,
		TotalSubscriptions: row.TotalSubscriptions}
	if row.StartDate != nil {
		text := DRFUTCDateTime(*row.StartDate)
		course.StartDate = &text
	}
	if row.EndDate != nil {
		text := DRFUTCDateTime(*row.EndDate)
		course.EndDate = &text
	}
	course.Tags = []domain.SearchProfileTag{}
	if err := json.Unmarshal([]byte(row.Tags), &course.Tags); err != nil {
		return course, err
	}
	course.Locations = []SearchProfileLocation{}
	err := json.Unmarshal([]byte(row.Locations), &course.Locations)
	return course, err
}
