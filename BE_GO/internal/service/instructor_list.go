package service

import (
	"context"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

type InstructorLists struct{ store domain.InstructorListStore }

func NewInstructorLists(store domain.InstructorListStore) *InstructorLists {
	return &InstructorLists{store: store}
}

// List follows instructor_list: the scope user's instructors for any role.
func (s *InstructorLists) List(ctx context.Context, identity domain.ResolvedIdentity) (map[string]any, error) {
	rows, err := s.store.Instructors(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	items := make([]dto.InstructorItem, 0, len(rows))
	for _, row := range rows {
		item, err := dto.InstructorItemFrom(row)
		if err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	return map[string]any{"data": items}, nil
}
