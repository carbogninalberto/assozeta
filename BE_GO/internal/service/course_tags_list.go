package service

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

type courseTagStore interface {
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	OrderedTags(ctx context.Context, association string) ([]domain.SearchProfileTag, error)
}

type CourseTagLists struct{ store courseTagStore }

func NewCourseTagLists(store courseTagStore) *CourseTagLists { return &CourseTagLists{store: store} }

// List follows course_tags_list: request.user.sport_association (athletes
// are refused, a missing association is a 500), tags ordered by name.
func (s *CourseTagLists) List(ctx context.Context, identity domain.ResolvedIdentity) (map[string]any, error) {
	if identity.ScopeUser.Role == domain.RoleAthlete {
		return nil, &domain.PermissionDenied{Detail: "User is not a sport association"}
	}
	association, err := s.store.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	tags, err := s.store.OrderedTags(ctx, association)
	if err != nil {
		return nil, err
	}
	return map[string]any{"tags": tags}, nil
}
