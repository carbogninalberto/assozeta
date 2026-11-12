package repository

import (
	"context"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/redis/go-redis/v9"
)

const djangoSessionPrefix = ":1:django.contrib.sessions.cache"

type DjangoSessionCache struct{ client *redis.Client }

func NewDjangoSessionCache(client *redis.Client) (*DjangoSessionCache, error) {
	if client == nil || client.Options().DB != 1 {
		return nil, errors.New("Django sessions require cache database 1")
	}
	if client.Options().MaxRetries != 0 || !client.Options().ContextTimeoutEnabled {
		return nil, errors.New("Django sessions require disabled retries and context timeouts")
	}
	return &DjangoSessionCache{client: client}, nil
}

var _ domain.DjangoSessions = (*DjangoSessionCache)(nil)

func (r *DjangoSessionCache) LoadSession(ctx context.Context, key string) (any, error) {
	// GETRANGE's inclusive end returns at most the budget plus one sentinel byte.
	// Bound the Redis response before allocation, not just after an unlimited GET.
	raw, err := r.client.GetRange(ctx, djangoSessionPrefix+key, 0, maxDjangoSessionBytes).Result()
	if err != nil {
		return nil, err
	}
	if raw == "" {
		return nil, nil
	}
	return decodeDjangoSession(ctx, []byte(raw))
}
func (r *DjangoSessionCache) DeleteSession(ctx context.Context, key string) error {
	return r.client.Del(ctx, djangoSessionPrefix+key).Err()
}
