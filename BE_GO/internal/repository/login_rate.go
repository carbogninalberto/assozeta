package repository

import (
	"context"
	"errors"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/redis/go-redis/v9"
)

type DjangoLoginRateCache struct{ client *redis.Client }

func NewDjangoLoginRateCache(client *redis.Client) (*DjangoLoginRateCache, error) {
	if client == nil || client.Options().DB != 1 {
		return nil, errors.New("login rate cache requires Django cache database 1")
	}
	// go-redis normalizes MaxRetries:-1 to zero when constructing the client.
	// Replaying an increment after a lost response can consume another attempt.
	if client.Options().MaxRetries != 0 || !client.Options().ContextTimeoutEnabled {
		return nil, errors.New("login rate cache requires disabled retries and context timeouts")
	}
	return &DjangoLoginRateCache{client: client}, nil
}

var _ domain.LoginRateCounter = (*DjangoLoginRateCache)(nil)

const incrementLoginRate = `if redis.call('EXISTS', KEYS[1]) == 1 then
return redis.call('INCRBY', KEYS[1], 1)
else return false end`

func (r *DjangoLoginRateCache) IncrementLoginRate(ctx context.Context, key string) (int64, error) {
	if len(key) != 67 || !strings.HasPrefix(key, "rl:") || strings.Trim(key[3:], "0123456789abcdef") != "" {
		return 0, errors.New("invalid login rate cache key")
	}
	if err := ctx.Err(); err != nil {
		return 0, err
	}
	key = ":1:" + key
	added, err := r.client.SetNX(ctx, key, 1, 65*time.Second).Result()
	if err != nil {
		return 0, err
	}
	if added {
		return 1, nil
	}
	// Match Django's add-then-increment sequence. If the existing key expires
	// between those commands, do not create an immortal counter or admit a retry.
	count, err := r.client.Eval(ctx, incrementLoginRate, []string{key}).Int64()
	if errors.Is(err, redis.Nil) {
		return 0, domain.ErrLoginRateCounterMissing
	}
	return count, err
}
