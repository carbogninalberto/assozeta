package repository

import (
	"strings"
	"testing"
)

// psycopg2 can build a Python datetime only for years 1..9999 in the UTC
// session Django opens; PostgreSQL stores wider timestamptz values (an import
// value near the end of year 9999 with a negative offset becomes year 10000 in
// UTC). The query flags such a row in SQL: the driver's text format cannot
// carry a five-digit year back to Go.
func TestOrphanMedicalPythonYear(t *testing.T) {
	test := orphanMedicalPythonYear(`"t"."c"`)
	for _, part := range []string{`date_part('year', "t"."c" AT TIME ZONE 'UTC')`, `BETWEEN 1 AND 9999`} {
		if !strings.Contains(test, part) {
			t.Errorf("%s lacks %s", test, part)
		}
	}
}
