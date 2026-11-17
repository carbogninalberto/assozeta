//go:build !handoffprobe

package handler

// apiSchemaHandoffProbe never refuses a language: the hand-off probe exists only
// in the handoffprobe test build (api_schema_handoff_probe.go).
func apiSchemaHandoffProbe(string) error { return nil }
