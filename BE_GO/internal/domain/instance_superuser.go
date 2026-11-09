package domain

import "errors"

// ErrInstanceSuperuserIntegrity is the IntegrityError create_instance_superuser
// reports as an existing account (for example two usernames equal after NFKC).
var ErrInstanceSuperuserIntegrity = errors.New("instance superuser integrity error")

// InstanceSuperuser is the row create_superuser saves: username already
// uppercased and NFKC-normalised, email with its domain lowercased.
type InstanceSuperuser struct {
	UserID, Username, Email, FirstName, LastName, Phone, PasswordHash string
}
