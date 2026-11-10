package config

import "errors"

// SubscriptionImportSettings are core.settings' member import values.
type SubscriptionImportSettings struct {
	// MaxUploadBytes is SUBSCRIPTION_IMPORT_MAX_UPLOAD_BYTES (env.int,
	// default 20 MiB): the largest accepted import file.
	MaxUploadBytes int64
	// RetentionHours is SUBSCRIPTION_IMPORT_RETENTION_HOURS (env.int, default
	// 24): how long an upload that was not imported is kept.
	RetentionHours int64
}

// MedicalCertificateUploadSettings are core.settings' values for
// document/medical/certificate/.
type MedicalCertificateUploadSettings struct {
	// MaxUploadBytes is MEDICAL_CERTIFICATE_MAX_UPLOAD_BYTES (env.int, default 10 MiB).
	MaxUploadBytes int64
	// UploadsPerHour is MEDICAL_CERTIFICATE_UPLOADS_PER_HOUR (env.int, default
	// 60): the requests one address may send without an account in an hour.
	UploadsPerHour int64
}

func parseMedicalCertificateUpload(values map[string]string) (MedicalCertificateUploadSettings, error) {
	settings := MedicalCertificateUploadSettings{MaxUploadBytes: 10 << 20, UploadsPerHour: 60}
	for key, target := range map[string]*int64{"MEDICAL_CERTIFICATE_MAX_UPLOAD_BYTES": &settings.MaxUploadBytes,
		"MEDICAL_CERTIFICATE_UPLOADS_PER_HOUR": &settings.UploadsPerHour} {
		text, present := values[key]
		if !present {
			continue
		}
		number, ok := pythonInt(text)
		if !ok {
			return settings, errors.New(key + " must be an integer")
		}
		*target = number
	}
	return settings, nil
}

func parseSubscriptionImport(values map[string]string) (SubscriptionImportSettings, error) {
	settings := SubscriptionImportSettings{MaxUploadBytes: 20 << 20, RetentionHours: 24}
	for key, target := range map[string]*int64{"SUBSCRIPTION_IMPORT_MAX_UPLOAD_BYTES": &settings.MaxUploadBytes,
		"SUBSCRIPTION_IMPORT_RETENTION_HOURS": &settings.RetentionHours} {
		text, present := values[key]
		if !present {
			continue
		}
		number, ok := pythonInt(text)
		if !ok {
			return settings, errors.New(key + " must be an integer")
		}
		*target = number
	}
	return settings, nil
}
