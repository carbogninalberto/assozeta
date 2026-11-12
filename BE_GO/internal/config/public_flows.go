package config

import "errors"

// PublicFlowSettings are core.settings' limits of the flows open without an
// account (form answers, form attachments, the subscription link).
type PublicFlowSettings struct {
	// MaxUploadBytes is PUBLIC_UPLOAD_MAX_BYTES (env.int, default 10 MiB).
	MaxUploadBytes int64
	// RequestsPerWindow is PUBLIC_REQUESTS_PER_WINDOW (env.int, default 30):
	// the requests one address may send to an endpoint without an account.
	RequestsPerWindow int64
	// WindowSeconds is PUBLIC_REQUEST_WINDOW_SECONDS (env.int, default 600).
	WindowSeconds int64
}

func parsePublicFlows(values map[string]string) (PublicFlowSettings, error) {
	settings := PublicFlowSettings{MaxUploadBytes: 10 << 20, RequestsPerWindow: 30, WindowSeconds: 600}
	for key, target := range map[string]*int64{"PUBLIC_UPLOAD_MAX_BYTES": &settings.MaxUploadBytes,
		"PUBLIC_REQUESTS_PER_WINDOW": &settings.RequestsPerWindow, "PUBLIC_REQUEST_WINDOW_SECONDS": &settings.WindowSeconds} {
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
