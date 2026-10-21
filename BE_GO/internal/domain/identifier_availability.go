package domain

import "errors"

var ErrIdentifierTooLong = errors.New("identifier exceeds existing field limit")

// IdentifierAccount contains exactly the fields exposed by check/email's
// existing get_user option. Credential hashes and permission fields are absent.
type IdentifierAccount struct {
	ID        string `db:"user_id"`
	FirstName string `db:"first_name"`
	LastName  string `db:"last_name"`
	Email     string `db:"email"`
}
type IdentifierAvailability struct {
	Available bool
	User      *IdentifierAccount
}
