package domain

// ConsumerBackupFailureOwner is what notify_failures reads from the first
// InstanceConfiguration: its domain and the primary association's owner
// e-mail (nil when there is no association, no owner or a NULL e-mail).
type ConsumerBackupFailureOwner struct {
	Domain string  `db:"domain"`
	Email  *string `db:"email"`
}
