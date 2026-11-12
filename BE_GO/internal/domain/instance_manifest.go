package domain

import "context"

// InstanceManifestBranding contains only the public branding fields used by
// the PWA manifest. Provider credentials and integration settings are not read.
type InstanceManifestBranding struct {
	Name         string `db:"name"`
	Abbreviation string `db:"abbreviation"`
	Description  string `db:"meta_description"`
	LogoPath     string `db:"logo_path"`
	PrimaryColor string `db:"primary_color"`
}

type InstanceManifestStore interface {
	FirstManifestBranding(context.Context) (*InstanceManifestBranding, error)
}
