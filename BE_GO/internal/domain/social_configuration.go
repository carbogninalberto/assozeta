package domain

import (
	"context"
	"encoding/json"
)

// Only the current instance fields used by social admission are read. Legacy
// google_client_id/apple_client_id columns are not effective overrides.
type SocialConfiguration struct {
	SelfHosted   bool            `db:"self_hosted"`
	Integrations json.RawMessage `db:"integration_settings"`
	Display      json.RawMessage `db:"display_settings"`
}

type SocialConfigurationReader interface {
	SocialConfiguration(context.Context) (*SocialConfiguration, error)
}
