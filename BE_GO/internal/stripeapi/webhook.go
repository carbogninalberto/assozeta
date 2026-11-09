package stripeapi

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"math/big"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// tolerance is Webhook.DEFAULT_TOLERANCE (seconds).
const tolerance = 300

var (
	// ErrWebhookPayload is a ValueError from construct_event: the payload is
	// not UTF-8 or not JSON.
	ErrWebhookPayload = errors.New("invalid webhook payload")
	// ErrWebhookSignature is stripe.error.SignatureVerificationError.
	ErrWebhookSignature = errors.New("invalid webhook signature")
	// ErrWebhookUnhandled is an exception construct_event lets through (a
	// non-ASCII signature reaching hmac.compare_digest, an event that is not a
	// JSON object): the view does not catch it.
	ErrWebhookUnhandled = errors.New("webhook construction failed")
)

// ConstructEvent is stripe.Webhook.construct_event(payload, header, secret):
// the payload is decoded as UTF-8, the header verified (nil: no header), then
// the payload parsed as JSON (Python semantics) into the event object.
func ConstructEvent(payload []byte, header *string, secret string, now time.Time) (domain.RequestObject, error) {
	if !utf8.Valid(payload) {
		return nil, ErrWebhookPayload
	}
	if err := verifyHeader(payload, header, secret, now); err != nil {
		return nil, err
	}
	parsed, err := dto.ParseProfileTablesJSON(payload)
	if err != nil {
		return nil, ErrWebhookPayload
	}
	event, ok := parsed.(domain.RequestObject)
	if !ok {
		return nil, ErrWebhookUnhandled
	}
	return event, nil
}

// verifyHeader is WebhookSignature.verify_header with the default tolerance.
func verifyHeader(payload []byte, header *string, secret string, now time.Time) error {
	if header == nil {
		return ErrWebhookSignature
	}
	// [i.split("=", 2) for i in header.split(",")]
	var items [][]string
	for _, item := range strings.Split(*header, ",") {
		items = append(items, strings.SplitN(item, "=", 3))
	}
	// int([i[1] for i in items if i[0] == "t"][0]): every "t" item must have a value.
	var stamps []string
	for _, item := range items {
		if item[0] == "t" {
			if len(item) < 2 {
				return ErrWebhookSignature
			}
			stamps = append(stamps, item[1])
		}
	}
	if len(stamps) == 0 {
		return ErrWebhookSignature
	}
	timestamp, ok := pythonInt(stamps[0])
	if !ok {
		return ErrWebhookSignature
	}
	var signatures []string
	for _, item := range items {
		if item[0] == "v1" {
			if len(item) < 2 {
				return ErrWebhookSignature
			}
			signatures = append(signatures, item[1])
		}
	}
	if len(signatures) == 0 {
		return ErrWebhookSignature
	}
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(timestamp.String() + "."))
	mac.Write(payload)
	expected := hex.EncodeToString(mac.Sum(nil))
	matched := false
	for _, signature := range signatures {
		// hmac.compare_digest refuses str arguments with non-ASCII characters (TypeError).
		for _, r := range signature {
			if r > unicode.MaxASCII {
				return ErrWebhookUnhandled
			}
		}
		if hmac.Equal([]byte(expected), []byte(signature)) {
			matched = true
			break
		}
	}
	if !matched {
		return ErrWebhookSignature
	}
	// timestamp < time.time() - tolerance
	limit := new(big.Float).SetFloat64(float64(now.UnixNano())/1e9 - tolerance)
	if new(big.Float).SetInt(timestamp).Cmp(limit) < 0 {
		return ErrWebhookSignature
	}
	return nil
}

// pythonInt is int(text) for a str: surrounding whitespace, an optional sign,
// decimal digits (any Unicode decimal digit) with single underscores between them.
func pythonInt(text string) (*big.Int, bool) {
	text = strings.TrimFunc(text, unicode.IsSpace)
	negative := false
	if strings.HasPrefix(text, "+") || strings.HasPrefix(text, "-") {
		negative = text[0] == '-'
		text = text[1:]
	}
	var digits strings.Builder
	previousDigit := false
	for _, r := range text {
		switch {
		case unicode.Is(unicode.Nd, r):
			digits.WriteRune('0' + digitValue(r))
			previousDigit = true
		case r == '_' && previousDigit:
			previousDigit = false
		default:
			return nil, false
		}
	}
	if digits.Len() == 0 || !previousDigit {
		return nil, false
	}
	value, ok := new(big.Int).SetString(digits.String(), 10)
	if !ok {
		return nil, false
	}
	if negative {
		value.Neg(value)
	}
	return value, true
}

// digitValue is the decimal value of a Unicode Nd character: Nd code points
// come in contiguous runs whose length is a multiple of ten, each starting at a zero.
func digitValue(r rune) rune {
	start := r
	for unicode.Is(unicode.Nd, start-1) {
		start--
	}
	return (r - start) % 10
}
