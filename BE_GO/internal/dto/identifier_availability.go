package dto

// Availability replies preserve the existing public envelope. Only check/email
// with a present get_user parameter can include IdentifierUser.
type IdentifierAvailability struct {
	Valid     bool                     `json:"valid"`
	Exception string                   `json:"exception,omitempty"`
	Data      *IdentifierAvailableData `json:"data,omitempty"`
	User      *IdentifierUser          `json:"user,omitempty"`
}
type IdentifierAvailableData struct {
	Message string `json:"msg"`
}
type IdentifierUser struct {
	FirstName string `json:"first_name"`
	LastName  string `json:"last_name"`
	Email     string `json:"email"`
	ID        string `json:"user_id"`
}
