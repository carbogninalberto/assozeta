package repository

import (
	"testing"
	"time"
)

// psycopg2 can build a Python datetime only for years 1..9999; PostgreSQL
// stores wider timestamptz values, which the association import can write.
func TestPythonDatetimeYear(t *testing.T) {
	for _, year := range []int{1, 1970, 9999} {
		if !pythonDatetimeYear(time.Date(year, 1, 1, 0, 0, 0, 0, time.UTC)) {
			t.Errorf("year %d rejected", year)
		}
	}
	for _, year := range []int{-1, 0, 10000} {
		if pythonDatetimeYear(time.Date(year, 1, 1, 0, 0, 0, 0, time.UTC)) {
			t.Errorf("year %d accepted", year)
		}
	}
	if !pythonDatetimeYear(nil) {
		t.Error("a non-timestamp value is not a conversion failure")
	}
}
