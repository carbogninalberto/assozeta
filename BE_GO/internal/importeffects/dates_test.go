package importeffects

import (
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// TestParseDateTimeMatchesDTO cross-checks parse_datetime against the same
// port already used by other families (dto.DjangoParseDateTimeWide, validated
// against CPython 3.13): the outcome (parsed/None/ValueError) and the UTC
// instant must agree.
func TestParseDateTimeMatchesDTO(t *testing.T) {
	corpus := []string{
		"2026-01-02 03:04:05", "2026-01-02T03:04:05", "2026-01-02", "20260102", "2026-W01-1", "2026W011",
		"2026-W01", "2026-01-02x10:00", "2026-01-02T10", "2026-01-02T1030", "2026-01-02T10:30:45.5",
		"2026-01-02T10:30:45.1234567", "2026-01-02T10:30:45,123", "2026-01-02T10:30Z", "2026-01-02T10:30z",
		"2026-01-02T10:30+0230", "2026-01-02T10:30+02:30:15", "2026-01-02T10:30-02", "2026-01-02T10:30+24:00",
		"2026-02-31 03:04:05", "2026-13-01 10:00", "2026-01-01T25:00:00", "hello", "", "2026-1-1", "2026-1-2 3:4:5",
		"2026-01-02 03:04:05\n", "2026-01-02 03:04:05\n\n", "2026-01-02\n", "٢٠٢٦-٠١-٠١",
		"0001-01-01 00:00:00+14:00", "9999-12-31 23:59:59.999999-12:00", "2026-01-02T10:30:45.1234567890",
		"2026-06-01T00:00:00.000000", "2026-01-02T10:30:45+02:30:15.5",
	}
	for _, text := range corpus {
		moment, err := parseDateTime(text)
		instant, aware, outcome := dto.DjangoParseDateTimeWide(text)
		switch {
		case err != nil:
			if outcome != dto.DjangoDateTimeValueError {
				t.Fatalf("%q: we raise %v, dto outcome %v", text, err, outcome)
			}
		case moment == nil:
			if outcome != dto.DjangoDateTimeNone {
				t.Fatalf("%q: we return None, dto outcome %v", text, outcome)
			}
		default:
			if outcome != dto.DjangoDateTimeParsed {
				t.Fatalf("%q: we parse, dto outcome %v", text, outcome)
			}
			if got := moment.utc(); !got.Equal(instant) {
				t.Fatalf("%q: instant %v, dto %v", text, got, instant)
			}
			if aware == moment.naive {
				t.Fatalf("%q: awareness %v, dto %v", text, !moment.naive, aware)
			}
		}
	}
}

// TestParseDateTimeWall checks the wall time of aware values with fractional
// offset seconds, which Go's fixed zones cannot hold alone.
func TestParseDateTimeWall(t *testing.T) {
	moment, err := parseDateTime("2026-01-02T03:04:05+02:30:15.5")
	if err != nil || moment == nil {
		t.Fatalf("%v %v", moment, err)
	}
	if got := moment.String(); got != "2026-01-02 03:04:05+02:30:15.500000" {
		t.Fatalf("wall %q", got)
	}
	if got := pythonNaive(moment.utc()); got != "2026-01-02 00:33:49.500000" {
		t.Fatalf("utc %q", got)
	}
}
