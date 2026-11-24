//go:build !handoff

package main

import "time"

func scheduleNow() (time.Time, error) { return time.Now().UTC().Truncate(time.Microsecond), nil }

func controlledClock() bool { return false }
