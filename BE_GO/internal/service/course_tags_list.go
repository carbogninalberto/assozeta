package service

import (
	"context"
	"encoding/json"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
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

type courseLocationStore interface {
	OwnerAssociation(ctx context.Context, owner string) (string, error)
	CourseLocations(ctx context.Context, association string) ([]domain.CourseLocationRow, error)
}

type CourseLocationLists struct{ store courseLocationStore }

func NewCourseLocationLists(store courseLocationStore) *CourseLocationLists {
	return &CourseLocationLists{store: store}
}

type courseLocationDocument struct {
	ID            string    `json:"document_id"`
	CreationDate  time.Time `json:"creation_date"`
	Filepath      *string   `json:"filepath"`
	Filename      string    `json:"filename"`
	FileSizeBytes *int64    `json:"file_size_bytes"`
	Token         string    `json:"token"`
}

// List follows CourseLocationViewSet.list: SportAssociation.objects.get(user=
// request.user) (a miss is a 500), then CourseLocationSerializer.
func (s *CourseLocationLists) List(ctx context.Context, identity domain.ResolvedIdentity) (map[string]any, error) {
	association, err := s.store.OwnerAssociation(ctx, identity.ScopeUser.ID)
	if err != nil {
		return nil, err
	}
	rows, err := s.store.CourseLocations(ctx, association)
	if err != nil {
		return nil, err
	}
	items := make([]map[string]any, 0, len(rows))
	for _, row := range rows {
		item, err := courseLocationItem(domain.CourseLocation{ID: row.ID, Title: row.Title, Description: row.Description, Address: row.Address,
			AssociationID: row.AssociationID}, row.Documents)
		if err != nil {
			return nil, err
		}
		items = append(items, item)
	}
	return map[string]any{"data": items}, nil
}

// courseLocationItem is CourseLocationSerializer(location).data; documents is
// the nested documents JSON.
func courseLocationItem(row domain.CourseLocation, documents string) (map[string]any, error) {
	decoded := []courseLocationDocument{}
	if err := json.Unmarshal([]byte(documents), &decoded); err != nil {
		return nil, err
	}
	rendered := make([]map[string]any, 0, len(decoded))
	for _, document := range decoded {
		rendered = append(rendered, map[string]any{"document_id": document.ID, "creation_date": dto.DRFUTCDateTime(document.CreationDate),
			"filepath": document.Filepath, "filename": document.Filename, "file_size_bytes": document.FileSizeBytes, "token": document.Token})
	}
	return map[string]any{"course_location_id": row.ID, "documents": rendered, "title": row.Title,
		"description": row.Description, "address": row.Address, "sport_association": row.AssociationID}, nil
}
