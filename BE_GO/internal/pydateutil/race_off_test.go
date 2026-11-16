//go:build !race

package pydateutil

// raceDetector is set under -race (race_on_test.go).
const raceDetector = false
