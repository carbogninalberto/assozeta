package service

import (
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// instance.sso.public_links.source_token accepts an aware expires_at whose UTC
// instant falls outside years 1..9999: Python keeps it with its own offset and
// compares it with now without error, so '9999-12-31T23:59:59-23:59' is a
// future grant even though its UTC instant is in year 10000.
func TestBakneyPublicLinkWideExpiry(t *testing.T) {
	expires, aware, outcome := dto.DjangoParseDateTimeWide("9999-12-31T23:59:59-23:59")
	if outcome != dto.DjangoDateTimeParsed || !aware {
		t.Fatalf("outcome %v aware %v, want a parsed aware value", outcome, aware)
	}
	if outside := dto.DjangoDateTimeOutside(expires); !outside {
		t.Fatalf("%s is inside years 1..9999", expires.UTC())
	}
	if !expires.After(time.Now()) {
		t.Fatalf("%s is not after now", expires.UTC())
	}
}
