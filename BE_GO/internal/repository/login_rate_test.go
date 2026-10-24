package repository

import (
	"github.com/redis/go-redis/v9"
	"testing"
)

func TestLoginRateCacheRequiresCompatibleClient(t *testing.T) {
	if _, err := NewDjangoLoginRateCache(nil); err == nil {
		t.Fatal("nil client accepted")
	}
	for _, options := range []redis.Options{
		{DB: 0, MaxRetries: -1, ContextTimeoutEnabled: true},
		{DB: 1, ContextTimeoutEnabled: true},
		{DB: 1, MaxRetries: -1},
	} {
		client := redis.NewClient(&options)
		if _, err := NewDjangoLoginRateCache(client); err == nil {
			t.Fatal("incompatible client accepted")
		}
		client.Close()
	}
}
