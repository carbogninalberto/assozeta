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
