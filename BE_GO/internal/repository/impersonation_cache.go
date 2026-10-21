package repository

import (
	"context"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"github.com/redis/go-redis/v9"
)

const ImpersonationTTL = time.Hour

type ImpersonationSessions interface {
	Get(context.Context, string) (domain.ImpersonationSession, error)
	Put(context.Context, string, domain.ImpersonationSession) error
	Delete(context.Context, string) error
}

type DjangoImpersonationCache struct{ client *redis.Client }

func NewDjangoImpersonationCache(client *redis.Client) (*DjangoImpersonationCache, error) {
	if client == nil || client.Options().DB != 1 {
		return nil, errors.New("impersonation cache requires Django cache database 1")
	}
	return &DjangoImpersonationCache{client: client}, nil
}

var _ ImpersonationSessions = (*DjangoImpersonationCache)(nil)

func impersonationCacheKey(identifier string) (string, error) {
	id, err := uuid.Parse(identifier)
	if err != nil {
		return "", domain.ErrInvalidImpersonation
	}
	return ":1:impersonation:v1:" + id.String(), nil
}
func (r *DjangoImpersonationCache) Get(ctx context.Context, id string) (domain.ImpersonationSession, error) {
	key, err := impersonationCacheKey(id)
	if err != nil {
		return domain.ImpersonationSession{}, err
	}
	// GET does not renew TTL. Never keep a process-local authorization cache.
	raw, err := r.client.Get(ctx, key).Bytes()
	if errors.Is(err, redis.Nil) {
		return domain.ImpersonationSession{}, domain.ErrImpersonationNotFound
	}
	if err != nil {
		return domain.ImpersonationSession{}, err
	}
	return decodeImpersonation(raw)
}
func (r *DjangoImpersonationCache) Put(ctx context.Context, id string, record domain.ImpersonationSession) error {
	key, err := impersonationCacheKey(id)
	if err != nil {
		return err
	}
	encoded, err := encodeImpersonation(record)
	if err != nil {
		return err
	}
	return r.client.Set(ctx, key, encoded, ImpersonationTTL).Err()
}
func (r *DjangoImpersonationCache) Delete(ctx context.Context, id string) error {
	key, err := impersonationCacheKey(id)
	if err != nil {
		return err
	}
	return r.client.Del(ctx, key).Err()
}
