//go:build !handoff

package whatsapp

import "time"

func pollInterval() time.Duration { return 10 * time.Second }
