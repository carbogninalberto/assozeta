package service

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"hash/crc32"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"golang.org/x/text/cases"
)

var ErrLoginRateLimited = errors.New("login rate limit exceeded")

type LoginRate struct {
	counter domain.LoginRateCounter
	now     func() time.Time
}

func NewLoginRate(counter domain.LoginRateCounter, now func() time.Time) *LoginRate {
	return &LoginRate{counter: counter, now: now}
}

func (s *LoginRate) CheckAccount(ctx context.Context, body any) error {
	object, ok := body.(domain.RequestObject)
	if !ok {
		return errors.New("login rate account body is not an object")
	}
	value, present := object.Lookup("username")
	if !present {
		value = ""
	}
	text, err := loginValueText(ctx, value)
	if err != nil {
		return err
	}
	text = cases.Fold().String(strings.TrimFunc(text, func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }))
	if text == "" {
		text = "missing-username"
	}
	return s.Check(ctx, "login-account", text)
}

// Check consumes one attempt for an already resolved request key. Transport
// admission must call login-ip before reading the body, then login-account.
func (s *LoginRate) Check(ctx context.Context, group, value string) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	var limit int64
	switch group {
	case "login-ip":
		limit = 20
	case "login-account":
		limit = 5
	default:
		return errors.New("unknown login rate group")
	}
	if !utf8.ValidString(value) {
		return errors.New("invalid login rate key encoding")
	}
	ts := s.now().Unix()
	// Python modulo stays nonnegative, including timestamps before the epoch.
	window := ts - (ts%60+60)%60 + int64(crc32.ChecksumIEEE([]byte(value))%60)
	if window < ts {
		window += 60
	}
	digest := sha256.Sum256([]byte(group + strconv.FormatInt(limit, 10) + "/60s" + value + strconv.FormatInt(window, 10) + "POST"))
	count, err := s.counter.IncrementLoginRate(ctx, "rl:"+hex.EncodeToString(digest[:]))
	if err := ctx.Err(); err != nil {
		return err
	}
	if errors.Is(err, domain.ErrLoginRateCounterMissing) {
		return ErrLoginRateLimited
	}
	if err != nil {
		return err
	}
	if count > limit {
		return ErrLoginRateLimited
	}
	return nil
}
