package dto

import (
	"encoding/json"
	"fmt"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

func DescribeImpersonationPerson(person domain.ImpersonationPerson, scope *domain.ImpersonationAssociation) (ImpersonationPerson, error) {
	role := map[int]string{domain.RoleAssociation: "association", domain.RoleAthlete: "athlete", domain.RoleCollaborator: "collaborator"}[person.Role]
	if role == "" {
		return ImpersonationPerson{}, fmt.Errorf("invalid impersonation target role")
	}
	result := ImpersonationPerson{ID: person.ID, Username: person.Username, Email: person.Email, FirstName: person.FirstName, LastName: person.LastName, Role: role, Association: scope}
	if person.LastLogin != nil {
		value := paymentCategoryResponseTime(*person.LastLogin)
		result.LastLogin = &value
	}
	if scope == nil && person.AssociationID != nil && person.AssociationName != nil {
		result.Association = &domain.ImpersonationAssociation{ID: *person.AssociationID, Name: *person.AssociationName}
	}
	return result, nil
}

type ImpersonationPerson struct {
	ID          string                           `json:"user_id"`
	Username    string                           `json:"username"`
	Email       string                           `json:"email"`
	FirstName   string                           `json:"first_name"`
	LastName    string                           `json:"last_name"`
	LastLogin   *string                          `json:"last_login"`
	Role        string                           `json:"role"`
	Association *domain.ImpersonationAssociation `json:"association"`
}

type ImpersonationList struct {
	Users    []ImpersonationPerson `json:"users"`
	NextPage *json.Number          `json:"next_page"`
	Meta     ImpersonationListMeta `json:"meta"`
}

type ImpersonationListMeta struct {
	Page    json.Number `json:"page"`
	PerPage int         `json:"perpage"`
	Total   int64       `json:"total"`
	Pages   int64       `json:"pages"`
}

type ImpersonationCurrent struct {
	Target *ImpersonationPerson `json:"target"`
}
type ImpersonationStarted struct {
	SessionID   string                           `json:"session_id"`
	Target      ImpersonationPerson              `json:"target"`
	ExpiresIn   int                              `json:"expires_in"`
	ActorRole   string                           `json:"actor_role"`
	Association *domain.ImpersonationAssociation `json:"association"`
}
