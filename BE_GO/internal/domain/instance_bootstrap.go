package domain

import (
	"context"
	"encoding/json"
	"errors"
	"time"
)

var ErrInstanceNotConfigured = errors.New("instance not configured")
var ErrInstanceConfigurationInvalid = errors.New("invalid instance configuration")

// InstanceBootstrapSnapshot contains public configuration and only the
// integration keys needed by public bootstrap; no encrypted credentials.
type InstanceBootstrapSnapshot struct {
	Name                        string          `db:"name"`
	Abbreviation                string          `db:"abbreviation"`
	PrimaryColor                string          `db:"primary_color"`
	SupportEmail                string          `db:"support_email"`
	LogoPath                    string          `db:"logo_path"`
	UpdatedAt                   time.Time       `db:"updated_at"`
	DisplaySettings             json.RawMessage `db:"display_settings" json:"-"`
	IntegrationKind             *string         `db:"integration_kind"`
	IntegrationsEmpty           bool            `db:"integrations_empty"`
	PublicIntegrations          json.RawMessage `db:"public_integrations" json:"-"`
	GoogleClientID              string          `db:"google_client_id"`
	AppleClientID               string          `db:"apple_client_id"`
	MetaTitle                   string          `db:"meta_title"`
	MetaDescription             string          `db:"meta_description"`
	IsReseller                  bool            `db:"is_reseller"`
	SelfHosted                  bool            `db:"self_hosted"`
	SupportMultipleAssociations bool            `db:"support_multiple_associations"`
	ShowCourseAthletes          bool            `db:"show_course_athletes"`
	ShowMinorExemption          bool            `db:"show_minor_exemption"`
	ShowTutorial                bool            `db:"show_tutorial"`
	SetupProvenance             string          `db:"setup_provenance"`
	OnboardingCompletedAt       *time.Time      `db:"onboarding_completed_at"`
	AssociationID               *string         `db:"association_id"`
	AssociationName             *string         `db:"association_name"`
	OwnerID                     *string         `db:"owner_id"`
}

type InstanceBootstrapStore interface {
	FirstInstance(context.Context) (*InstanceBootstrapSnapshot, error)
}
