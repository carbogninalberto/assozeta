package domain

// ConsumerEmailInstance is the InstanceConfiguration row
// instance.email_configuration.effective_email reads in the Celery worker.
type ConsumerEmailInstance struct {
	SelfHosted        bool   `db:"self_hosted"`
	EmailSettings     string `db:"email_settings"`
	PasswordEncrypted string `db:"email_password_encrypted"`
	Revision          int64  `db:"email_revision"`
}
