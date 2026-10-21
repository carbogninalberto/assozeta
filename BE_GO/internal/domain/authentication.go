package domain

// AuthenticationFailure describes credential admission, before identity scoping.
// HTTP rendering (including SimpleJWT token-message details) belongs to handlers.
type AuthenticationFailure struct{ Code, Detail string }

func (e *AuthenticationFailure) Error() string { return e.Detail }
