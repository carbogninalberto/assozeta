package redisclient

import (
	"crypto/tls"
	"crypto/x509"
	"errors"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/config"
	"github.com/redis/go-redis/v9"
	"os"
	"time"
)

// NewBroker opens the application's Celery Redis client (database 0).
func NewBroker(cfg config.Config) (*redis.Client, error) {
	options := &redis.Options{Addr: cfg.RedisAddress, Username: cfg.RedisUsername, Password: cfg.RedisPassword,
		DB: 0, MaxRetries: -1, DialTimeout: 2 * time.Second, ReadTimeout: 2 * time.Second, WriteTimeout: 2 * time.Second, ContextTimeoutEnabled: true}
	if cfg.RedisTLS {
		options.TLSConfig = &tls.Config{MinVersion: tls.VersionTLS12}
		if cfg.RedisRootCertificate != "" {
			pem, err := os.ReadFile(cfg.RedisRootCertificate)
			if err != nil {
				return nil, errors.New("cannot read Redis root certificate")
			}
			roots, err := x509.SystemCertPool()
			if err != nil {
				roots = x509.NewCertPool()
			}
			if !roots.AppendCertsFromPEM(pem) {
				return nil, errors.New("invalid Redis root certificate")
			}
			options.TLSConfig.RootCAs = roots
		}
	}
	options.Dialer = DialContext(options)
	return redis.NewClient(options), nil
}
