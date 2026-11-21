//go:build !handoff

package handoff

// Barrier observes protocol boundaries only in handoff test builds.
func Barrier(point, key string) {}
