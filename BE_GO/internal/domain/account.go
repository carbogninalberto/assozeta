package domain

import (
	"encoding/json"
	"errors"
)

var ErrAccountNotFound = errors.New("account not found")

const (
	RoleAssociation  = 1
	RoleAthlete      = 2
	RoleCollaborator = 3
)

// Account is an account row, not an authenticated or authorized request identity.
// The repository excludes soft-deleted rows but keeps Active=false distinguishable.
// Actor, effective person and tenant owner must be resolved separately by services.
type Account struct {
	ID               string  `db:"user_id"`
	Username         string  `db:"username"`
	Email            string  `db:"email"`
	Password         string  `db:"password" json:"-"`
	Role             int     `db:"role"`
	Active           bool    `db:"is_active"`
	Deleted          bool    `db:"deleted"`
	Superuser        bool    `db:"is_superuser"`
	ConnectedUserID  *string `db:"connected_user_id"`
	CollaboratorRole int     `db:"collaborator_role"`
	// Preserve the stored JSON value; identity services must validate it before
	// constructing a PermissionSubject. SQL NULL and JSON null both mean None in Django.
	Permissions json.RawMessage `db:"collaborator_permissions"`
}
