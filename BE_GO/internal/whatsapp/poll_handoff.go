//go:build handoff

package whatsapp

import (
	"os"
	"time"
)

func pollInterval() time.Duration {
	if interval, err := time.ParseDuration(os.Getenv("ASSOZETA_HANDOFF_WHATSAPP_POLL")); err == nil && interval > 0 {
		return interval
	}
	return 10 * time.Second
}
