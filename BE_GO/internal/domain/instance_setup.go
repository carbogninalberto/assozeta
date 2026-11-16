package domain

import (
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrInstanceSetupUnexpected is a state only the application writes: the
// association id of a stored import task result is not a UUID, while every
// writer stores str() of one (BE/application/tasks.py:2186,2193 and the
// send_mail/import workers of BE_GO). Django's UUIDField lookup raises a
// ValidationError no request can produce, so Go answers the server error page
// instead of handing the request off.
var ErrInstanceSetupUnexpected = refusal.AppState("instance setup: stored association id is not a UUID")

// ErrInstanceSetupExists is InstanceConfiguration.save's singleton check
// failing inside the setup transaction (a configuration appeared after the
// view's is_configured check).
var ErrInstanceSetupExists = errors.New("Only one InstanceConfiguration can exist. Use get_config() to retrieve it.")

// InstanceSetupConfiguration is the first InstanceConfiguration as the setup
// views' permissions read it: the primary association's owner, if any.
type InstanceSetupConfiguration struct {
	ID      int64   `db:"id"`
	OwnerID *string `db:"owner_id"`
}

// InstanceSetupFresh is what InstanceSetupView creates for a fresh setup.
// InstanceSetupImported is the configuration an import setup creates and
// the imported association it links.
type InstanceSetupImported struct {
	Domain, Name, Abbreviation, PrimaryColor, SupportEmail, LogoPath string
	GoogleClientID, AppleClientID                                    string
	AssociationID                                                    string
}

type InstanceSetupFresh struct {
	Domain, Name, Abbreviation, PrimaryColor, SupportEmail, LogoPath  string
	GoogleClientID, AppleClientID                                     string
	AssociationName, OwnerEmail, PasswordHash, Username               string
	UserID, AssociationID, CashAccountID, BankAccountID, OnboardingID string
}
