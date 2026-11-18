//go:build handoffprobe

package handler

import "github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"

// errAPISchemaHandoffProbe is the hand-off probe's refusal. It exists only in
// the handoffprobe test build: the django-handoff family needs one refused input
// to replay to Django, and every production `lang` is answered natively.
var errAPISchemaHandoffProbe = refusal.New("api schema: hand-off probe (test build only)")

// apiSchemaHandoffProbe returns the refusal sentinel for the probe language.
// The other builds always return nil.
func apiSchemaHandoffProbe(lang string) error {
	if lang == "handoff-probe" {
		return errAPISchemaHandoffProbe
	}
	return nil
}
