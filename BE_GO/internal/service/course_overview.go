package service

import (
	"context"
	"encoding/json"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type courseOverviewStore interface {
	CourseOverview(ctx context.Context, id, owner string, group *string) (*domain.CourseOverviewRow, error)
}

type CourseOverviews struct{ store courseOverviewStore }

func NewCourseOverviews(store courseOverviewStore) *CourseOverviews {
	return &CourseOverviews{store: store}
}

func courseOverviewJSON(value *string) any {
	if value == nil {
		return nil
	}
	return json.RawMessage(*value)
}

// Overview follows course_overview: is_valid_uuid(uid), then the live,
// group-aware course of request.user's association through CourseSerializer.
func (s *CourseOverviews) Overview(ctx context.Context, identity domain.ResolvedIdentity, raw, group string) (map[string]any, error) {
	id, err := paymentInfoParseUUID(raw)
	if err != nil {
		return nil, err
	}
	row, err := s.store.CourseOverview(ctx, id.String(), identity.ScopeUser.ID, supplierGroup(group))
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, domain.ErrCourseOverviewNotFound
	}
	var startDate, endDate any
	if row.StartDate != nil {
		startDate = dto.DRFUTCDateTime(*row.StartDate)
	}
	if row.EndDate != nil {
		endDate = dto.DRFUTCDateTime(*row.EndDate)
	}
	course := map[string]any{
		"course_id": row.ID,
		// AthleteSerializer(allow_null=True, many=True) on a missing attribute.
		"athletes": nil, "tags": json.RawMessage(row.Tags), "locations": json.RawMessage(row.Locations),
		"status_flag": row.StatusFlag, "course_type": row.CourseType, "creation_date": dto.DRFUTCDateTime(row.CreationDate),
		"title": row.Title, "description": row.Description, "multi_payments": row.MultiPayments, "fee": row.Fee,
		"events": courseOverviewJSON(row.Events), "one_fee_payment": row.OneFeePayment, "one_fee": row.OneFee,
		"google_calendar_id": row.GoogleCalendarID, "google_background_color": row.Color,
		"multiple_quotes": courseOverviewJSON(row.MultipleQuotes), "start_date": startDate, "end_date": endDate, "pinned": row.Pinned,
		"billed_duration_is_sport_season": row.SportSeason, "billed_frequency": row.BilledFrequency,
		"billed_from_subscription_date": row.FromSubscription, "billed_from_day_of_month": row.BilledDay, "auto_renewal": row.AutoRenewal,
		"deleted": row.Deleted, "sport_association": row.AssociationID, "group": row.GroupID,
	}
	return map[string]any{"data": map[string]any{"course": course}}, nil
}
