package repository

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

// ConsumerBackupFailures reads the instance owner and keeps the
// instance:backup-failure-notified:<key> marks in Django's cache (django-redis,
// database 1, key prefix ':1:').
type ConsumerBackupFailures struct {
	db    *sqlx.DB
	cache *redis.Client
}

func NewConsumerBackupFailures(db *sqlx.DB, cache *redis.Client) (*ConsumerBackupFailures, error) {
	if db == nil || cache == nil || cache.Options().DB != 1 {
		return nil, errors.New("backup failure notices require the database and Django cache database 1")
	}
	return &ConsumerBackupFailures{db: db, cache: cache}, nil
}

const consumerBackupFailuresKey = ":1:instance:backup-failure-notified:"

// consumerBackupFailuresTrue is pickle.dumps(True, 4), the value django-redis
// stores for a bool (pickle.DEFAULT_PROTOCOL on Python 3.13).
var consumerBackupFailuresTrue = []byte{0x80, 0x04, 0x88, '.'}

// BackupFailureOwner is InstanceConfiguration.objects.select_related(
// 'primary_association__user').first(): the lowest id, the association and
// user joined without their managers' filters.
func (r *ConsumerBackupFailures) BackupFailureOwner(ctx context.Context) (*domain.ConsumerBackupFailureOwner, error) {
	var owner domain.ConsumerBackupFailureOwner
	err := r.db.GetContext(ctx, &owner, `SELECT c.domain, u.email
 FROM instance_instanceconfiguration c
 LEFT JOIN application_sportassociation a ON a.sport_association_id = c.primary_association_id
 LEFT JOIN bakney_user u ON u.user_id = a.user_id
 ORDER BY c.id LIMIT 1`)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	return &owner, nil
}

// AddNotified is cache.add(key, True, timeout=7 * 86400): false when the key exists.
func (r *ConsumerBackupFailures) AddNotified(ctx context.Context, key string) (bool, error) {
	return r.cache.SetNX(ctx, consumerBackupFailuresKey+key, consumerBackupFailuresTrue, 7*24*time.Hour).Result()
}

// DeleteNotified is cache.delete(key).
func (r *ConsumerBackupFailures) DeleteNotified(ctx context.Context, key string) error {
	return r.cache.Del(ctx, consumerBackupFailuresKey+key).Err()
}
