package repository

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"

	"github.com/jmoiron/sqlx"
	"github.com/redis/go-redis/v9"
)

type DatabaseProbe struct{ db *sqlx.DB }

func NewDatabaseProbe(db *sqlx.DB) *DatabaseProbe        { return &DatabaseProbe{db: db} }
func (p *DatabaseProbe) Check(ctx context.Context) error { return p.db.PingContext(ctx) }

type RedisProbe struct{ client *redis.Client }

func NewRedisProbe(client *redis.Client) *RedisProbe { return &RedisProbe{client: client} }

// Redis scripts run without other keyspace commands interleaving, so a
// successful probe never exposes its temporary key to a cache snapshot. NX
// protects existing data even on a nonce collision. Scripts do not roll back
// writes on error: retain the ten-second expiry if cleanup is denied/fails.
const redisReadinessScript = `if not redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', 10) then
    return redis.error_reply('cache readiness key collision')
end
local value = redis.pcall('GET', KEYS[1])
local deleted = redis.pcall('DEL', KEYS[1])
if type(value) == 'table' and value.err then return value end
if type(deleted) == 'table' and deleted.err then return deleted end
if value ~= ARGV[1] then return redis.error_reply('cache readback failed') end
if deleted ~= 1 then return redis.error_reply('cache cleanup failed') end
return value`

func (p *RedisProbe) Check(ctx context.Context) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if p.client == nil {
		return errors.New("cache readiness requires a client")
	}
	// Match the production client's bounded I/O contract; never replay an EVAL
	// whose response was lost or detach it from the dependency/request deadline.
	if p.client.Options().MaxRetries != 0 || !p.client.Options().ContextTimeoutEnabled {
		return errors.New("cache readiness requires disabled retries and context timeouts")
	}
	nonce := make([]byte, 16)
	if _, err := rand.Read(nonce); err != nil {
		return err
	}
	key := "assozeta:go:readiness:" + hex.EncodeToString(nonce)
	value, err := p.client.Eval(ctx, redisReadinessScript, []string{key}, "ok").Text()
	if err != nil {
		return err
	}
	if value != "ok" {
		return errors.New("cache readback failed")
	}
	return ctx.Err()
}
