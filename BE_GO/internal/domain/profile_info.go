package domain

import "context"

// ProfileInfoDetailsReader reads only the admitted person and association owner.
type ProfileInfoDetailsReader interface {
	Read(context.Context, string, string) (ProfileInfoDetails, error)
}
type ProfileInfoDetails struct {
	UserID                   string  `db:"user_id"`
	AssociationID            string  `db:"sport_association_id"`
	TemporaryInvoiceDeletion bool    `db:"temporary_invoice_deletion"`
	ReviewURL                *string `db:"review_url"`
	ReviewURLEnabled         bool    `db:"review_url_enabled"`
}
