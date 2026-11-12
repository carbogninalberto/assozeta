package dto

type InstanceManifestIcon struct {
	Source string `json:"src"`
	Sizes  string `json:"sizes"`
	Type   string `json:"type"`
}

type InstanceManifest struct {
	Name            string                 `json:"name"`
	ShortName       string                 `json:"short_name"`
	Description     string                 `json:"description"`
	Icons           []InstanceManifestIcon `json:"icons"`
	StartURL        string                 `json:"start_url"`
	Display         string                 `json:"display"`
	BackgroundColor string                 `json:"background_color"`
	ThemeColor      string                 `json:"theme_color"`
}
