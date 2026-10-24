package dto

// ManualSections preserves the existing public projection and development
// preview section objects, including their nullable and nested JSON fields.
type ManualSections struct {
	Status  string `json:"status"`
	Results []any  `json:"results"`
	Message string `json:"message"`
}
