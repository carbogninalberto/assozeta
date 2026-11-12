package service

import (
	"context"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type EmailLogs struct {
	store    domain.EmailLogStore
	now      func() time.Time
	location *time.Location
}

func NewEmailLogs(store domain.EmailLogStore, now func() time.Time, location *time.Location) *EmailLogs {
	return &EmailLogs{store: store, now: now, location: location}
}

// DjangoNaiveNow reproduces naive datetime.now() under USE_TZ=True: the process
// wall clock in location, at microsecond precision, interpreted as UTC.
func DjangoNaiveNow(now time.Time, location *time.Location) time.Time {
	wall := now.In(location)
	return time.Date(wall.Year(), wall.Month(), wall.Day(), wall.Hour(), wall.Minute(), wall.Second(),
		wall.Nanosecond()/1000*1000, time.UTC)
}

// emailLogsOwner follows User.sport_association: athletes are denied,
// collaborators use their connected owner, every other role uses itself.
func emailLogsOwner(identity domain.ResolvedIdentity) (string, error) {
	user := identity.ScopeUser
	switch {
	case user == nil || user.ID == "":
		return "", domain.ErrInvalidScopedRequest
	case user.Role == domain.RoleAthlete:
		return "", domain.ErrEmailLogsNotAssociation
	case user.Role == domain.RoleCollaborator:
		if user.ConnectedUserID == nil {
			return "", domain.ErrAssociationNotFound
		}
		return *user.ConnectedUserID, nil
	}
	return user.ID, nil
}

func (s *EmailLogs) List(ctx context.Context, identity domain.ResolvedIdentity) (dto.EmailLogsList, error) {
	owner, err := emailLogsOwner(identity)
	if err != nil {
		return dto.EmailLogsList{}, err
	}
	now := DjangoNaiveNow(s.now(), s.location)
	rows, today, err := s.store.EmailLogs(ctx, owner, now.Add(-90*24*time.Hour), now.Add(-24*time.Hour))
	if err != nil {
		return dto.EmailLogsList{}, err
	}
	return dto.EmailLogsListFrom(rows, today), nil
}
