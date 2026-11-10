package main

import (
	"errors"
	"log/slog"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/handoff"
	"github.com/redis/go-redis/v9"
)

// composeHandoff reads DJANGO_HANDOFF_URL (empty: disabled) and the optional
// DJANGO_HANDOFF_MAX_BODY_BYTES (default handoff.DefaultMaxBody).
func composeHandoff(getenv func(string) string) (*handoff.Handoff, error) {
	maxBody := int64(0)
	if raw := strings.TrimSpace(getenv("DJANGO_HANDOFF_MAX_BODY_BYTES")); raw != "" {
		value, err := strconv.ParseInt(raw, 10, 64)
		if err != nil || value <= 0 {
			return nil, errors.New("DJANGO_HANDOFF_MAX_BODY_BYTES must be a positive integer")
		}
		maxBody = value
	}
	h, err := handoff.New(getenv("DJANGO_HANDOFF_URL"), maxBody, slog.Default())
	if err != nil {
		return nil, err
	}
	if h.Enabled() {
		slog.Info("refused API requests are handed off to Django")
	}
	return h, nil
}

// handoffRedisEffects records request-scoped Redis writes as handoff effects.
func handoffRedisEffects(h *handoff.Handoff, clients ...*redis.Client) {
	if !h.Enabled() {
		return
	}
	for _, client := range clients {
		client.AddHook(handoff.RedisHook{})
	}
}
