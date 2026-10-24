package repository

import (
	"github.com/redis/go-redis/v9"
	"testing"
)

func TestSessionCacheRequiresBoundedClient(t *testing.T) {
	if _, err := NewDjangoSessionCache(nil); err == nil {
		t.Fatal("nil cache accepted")
	}
	for _, options := range []redis.Options{
		{DB: 0, MaxRetries: -1, ContextTimeoutEnabled: true},
		{DB: 1, ContextTimeoutEnabled: true},
		{DB: 1, MaxRetries: -1},
	} {
		client := redis.NewClient(&options)
		if _, err := NewDjangoSessionCache(client); err == nil {
			t.Error("unbounded or incompatible cache accepted")
		}
		client.Close()
	}
}
