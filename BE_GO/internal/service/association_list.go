package service

import (
	"context"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// ErrAssociationListNotSuperuser is the view's bare 401 for a scope user
// that is not a superuser.
var ErrAssociationListNotSuperuser = errors.New("association list requires a superuser")

type AssociationList struct{ store domain.AssociationListStore }

func NewAssociationList(store domain.AssociationListStore) *AssociationList {
	return &AssociationList{store: store}
}

// List follows sport_association_list and SportAssociationToolSerializer. The
// notes source evaluates User.sport_association per row: an athlete owner
// raises PermissionDenied for the whole response, and anything but exactly
// one active association for the notes owner raises (500).
func (s *AssociationList) List(ctx context.Context, identity domain.ResolvedIdentity) ([]dto.AssociationListItem, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	if identity.ScopeUser == nil || !identity.ScopeUser.Superuser {
		return nil, ErrAssociationListNotSuperuser
	}
	rows, err := s.store.AssociationList(ctx, paymentCategoryAssociationScope(identity))
	if err != nil {
		return nil, err
	}
	items := make([]dto.AssociationListItem, 0, len(rows))
	for _, row := range rows {
		if row.OwnerRole == domain.RoleAthlete {
			return nil, &domain.PermissionDenied{Detail: domain.ErrEmailLogsNotAssociation.Error()}
		}
		if row.NotesCount != 1 {
			return nil, domain.ErrAssociationListNotes
		}
		items = append(items, dto.AssociationListItemFrom(row))
	}
	return items, nil
}
