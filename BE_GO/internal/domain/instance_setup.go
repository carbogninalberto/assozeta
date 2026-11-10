package domain

import (
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrInstanceSetupRefused marks an instance setup request the Go port does
// not serve (an import whose task has a ready result, values the database
// columns would reject inside Django's transaction, a domain that needs
// Python's Unicode lowercasing, URL values that need the IDNA fallback, a
// storage name collision after the old logos are deleted). Handlers answer
// with the server error page before any write.
var ErrInstanceSetupRefused = refusal.New("instance setup request not ported")

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
