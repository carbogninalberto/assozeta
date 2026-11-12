package dto

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

type Collaborator struct {
	ID               *uuid.UUID `json:"user_id"`
	FirstName        string     `json:"first_name"`
	LastName         string     `json:"last_name"`
	Email            string     `json:"email"`
	Phone            *string    `json:"phone"`
	Role             *int       `json:"role"`
	ConnectedUser    *uuid.UUID `json:"connected_user"`
	CollaboratorRole *int       `json:"collaborator_role"`
	Permissions      any        `json:"collaborator_permissions"`
	IsInvite         *bool      `json:"is_invite,omitempty"`
}
type CollaborationInvite struct {
	ID               uuid.UUID `json:"collaboration_invite_id"`
	Email            string    `json:"email"`
	Expires          *string   `json:"expiration_date"`
	Accepted         bool      `json:"accepted"`
	Token            *string   `json:"token"`
	CollaboratorRole int       `json:"collaborator_role"`
	Permissions      any       `json:"collaborator_permissions"`
	IsInvite         bool      `json:"is_invite"`
}

func EmptyCollaborator() Collaborator { empty := ""; return Collaborator{Phone: &empty} }
func CollaboratorFromRecord(row domain.CollaboratorRecord, list bool) (Collaborator, error) {
	permissions, err := collaboratorJSON(row.Permissions)
	if err != nil {
		return Collaborator{}, err
	}
	value := Collaborator{ID: &row.ID, FirstName: row.FirstName, LastName: row.LastName, Email: row.Email, Phone: row.Phone, Role: &row.Role, ConnectedUser: row.ConnectedUser, CollaboratorRole: &row.CollaboratorRole, Permissions: permissions}
	if list {
		flag := false
		value.IsInvite = &flag
	}
	return value, nil
}
func CollaborationInviteFromRecord(row domain.CollaborationInviteRecord) (CollaborationInvite, error) {
	permissions, err := collaboratorJSON(row.Permissions)
	if err != nil {
		return CollaborationInvite{}, err
	}
	var expires *string
	if row.Expires != nil {
		at := row.Expires.UTC()
		text := at.Format("2006-01-02T15:04:05")
		if micro := at.Nanosecond() / 1000; micro != 0 {
			text += fmt.Sprintf(".%06d", micro)
		}
		text += "Z"
		expires = &text
	}
	return CollaborationInvite{row.ID, row.Email, expires, row.Accepted, row.Token, row.CollaboratorRole, permissions, true}, nil
}

// JSONField preserves arbitrary integers while fractional tokens become Python
// binary64. Retain a fractional token for integral floats (1.0 is not JSON 1).
func collaboratorJSON(raw []byte) (any, error) {
	if len(raw) == 0 {
		return nil, nil
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	return collaboratorNumbers(value)
}
func collaboratorNumbers(value any) (any, error) {
	switch v := value.(type) {
	case json.Number:
		if !strings.ContainsAny(string(v), ".eE") {
			return v, nil
		}
		number, err := strconv.ParseFloat(string(v), 64)
		if math.IsInf(number, 0) || math.IsNaN(number) || (err != nil && number != 0) {
			return nil, errors.New("invalid persisted collaborator JSON number")
		}
		text := strconv.FormatFloat(number, 'g', -1, 64)
		if !strings.ContainsAny(text, ".eE") {
			text += ".0"
		}
		return json.Number(text), nil
	case []any:
		for i, item := range v {
			next, err := collaboratorNumbers(item)
			if err != nil {
				return nil, err
			}
			v[i] = next
		}
		return v, nil
	case map[string]any:
		for key, item := range v {
			next, err := collaboratorNumbers(item)
			if err != nil {
				return nil, err
			}
			v[key] = next
		}
		return v, nil
	default:
		return value, nil
	}
}
