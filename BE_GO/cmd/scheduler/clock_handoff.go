//go:build handoff

package main

import (
	"os"
	"strings"
	"time"
)

func scheduleNow() (time.Time, error) {
	path := os.Getenv("ASSOZETA_HANDOFF_CLOCK_FILE")
	if path == "" {
		return time.Now().UTC().Truncate(time.Microsecond), nil
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return time.Time{}, err
	}
	return time.Parse(time.RFC3339Nano, strings.TrimSpace(string(b)))
}

func controlledClock() bool { return os.Getenv("ASSOZETA_HANDOFF_CLOCK_FILE") != "" }
