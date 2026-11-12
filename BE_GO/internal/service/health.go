package service

import (
	"context"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/repository"
	"time"
)

type HealthService struct {
	database repository.DependencyProbe
	cache    repository.DependencyProbe
	timeout  time.Duration
}

func NewHealthService(database, cache repository.DependencyProbe, timeout time.Duration) *HealthService {
	return &HealthService{database: database, cache: cache, timeout: timeout}
}
func (s *HealthService) Readiness(ctx context.Context) domain.Readiness {
	// Separate bounded contexts ensure a database timeout cannot suppress the
	// Redis observation. Neither probe starts a background goroutine.
	databaseContext, cancelDatabase := context.WithTimeout(ctx, s.timeout)
	databaseOK := s.database.Check(databaseContext) == nil
	cancelDatabase()
	cacheContext, cancelCache := context.WithTimeout(ctx, s.timeout)
	cacheOK := s.cache.Check(cacheContext) == nil
	cancelCache()
	result := domain.Readiness{Status: "unavailable", Checks: domain.DependencyStatus{Database: databaseOK, Redis: cacheOK}}
	if result.Ready() {
		result.Status = "ready"
	}
	return result
}
