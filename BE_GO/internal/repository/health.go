package repository

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"time"

	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

type DatabaseProbe struct{ db *sqlx.DB }

func NewDatabaseProbe(db *sqlx.DB) *DatabaseProbe        { return &DatabaseProbe{db: db} }
func (p *DatabaseProbe) Check(ctx context.Context) error { return p.db.PingContext(ctx) }

type RedisProbe struct{ client *redis.Client }

func NewRedisProbe(client *redis.Client) *RedisProbe { return &RedisProbe{client: client} }
func (p *RedisProbe) Check(ctx context.Context) error {
	// A unique short-lived key tests both writes and reads and cannot overwrite a
	// Django pickle or race another replica's readiness request.
	nonce := make([]byte, 16)
	if _, err := rand.Read(nonce); err != nil {
		return err
	}
	key := "assozeta:go:readiness:" + hex.EncodeToString(nonce)
	if err := p.client.Set(ctx, key, "ok", 10*time.Second).Err(); err != nil {
		return err
	}
	value, err := p.client.Get(ctx, key).Result()
	if err != nil {
		return err
	}
	if value != "ok" {
		return errors.New("cache readback failed")
	}
	return nil
}
