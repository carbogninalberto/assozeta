package repository

// familyTaxCodeFilled is the filter of Django's family_tax_codes on an
// associate alias: only a filled-in tax code links a user's subscriptions
// to their family's.
func familyTaxCodeFilled(alias string) string {
	return alias + ".tax_code IS NOT NULL AND " + alias + ".tax_code<>''"
}
