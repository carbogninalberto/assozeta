package handler

import (
	"math"
	"net/http"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/service"
	"github.com/labstack/echo/v4"
)

// PublicFlowLimits are the limits of the flows open without an account:
// PublicRequestThrottle (requests per address and endpoint in a window,
// counted in the DRF throttle cache) and PUBLIC_UPLOAD_MAX_BYTES.
type PublicFlowLimits struct {
	Store          BakneyThrottleStore
	Requests       int
	WindowSeconds  int
	MaxUploadBytes int64
	Now            func() time.Time
}

// Allow is AnonRateThrottle.allow_request for the endpoint's scope plus DRF's
// Throttled response; requests with an account are not counted. It runs
// before the method dispatch, like check_throttles. A nil receiver or store
// does not throttle.
func (l *PublicFlowLimits) Allow(c echo.Context, scope string, identity *domain.ResolvedIdentity) (bool, error) {
	if l == nil || l.Store == nil || identity.ScopeUser != nil {
		return true, nil
	}
	return drfAnonThrottle(c, l.Store, l.Now, "throttle_"+scope+"_", l.Requests, l.WindowSeconds)
}

// BodyTooLarge answers the 413 of a request without an account that declares
// a body above the upload limit.
func (l *PublicFlowLimits) BodyTooLarge(c echo.Context, identity *domain.ResolvedIdentity) (bool, error) {
	if l == nil || identity.ScopeUser != nil || l.MaxUploadBytes <= 0 || c.Request().ContentLength <= l.MaxUploadBytes {
		return false, nil
	}
	return true, c.JSON(http.StatusRequestEntityTooLarge, map[string]string{"error": service.PublicUploadTooLarge(l.MaxUploadBytes)})
}

// drfAnonThrottle is SimpleRateThrottle.allow_request over the cache history
// of `prefix + ident`, with DRF's Throttled answer when the history is full.
func drfAnonThrottle(c echo.Context, store BakneyThrottleStore, nowFunc func() time.Time, prefix string, rate, window int) (bool, error) {
	request := c.Request()
	key := prefix + throttleIdent(request)
	history, err := store.History(request.Context(), key)
	if err != nil {
		return false, messageReadsError(c, err)
	}
	now := float64(nowFunc().UnixNano()) / 1e9
	duration := float64(window)
	for len(history) > 0 && history[len(history)-1] <= now-duration {
		history = history[:len(history)-1]
	}
	if len(history) >= rate {
		detail := "Request was throttled."
		if restLanguage(request) != "en" {
			detail = "La richiesta è stata limitata (throttled)."
		}
		remaining := duration
		if len(history) > 0 {
			remaining = duration - (now - history[len(history)-1])
		}
		if available := rate - len(history) + 1; available > 0 {
			wait := int(math.Ceil(remaining / float64(available)))
			unit := "seconds"
			if wait == 1 {
				unit = "second"
			}
			detail += " Expected available in " + strconv.Itoa(wait) + " " + unit + "."
			c.Response().Header().Set("Retry-After", strconv.Itoa(wait))
		}
		return false, c.JSON(http.StatusTooManyRequests, map[string]string{"detail": detail})
	}
	history = append([]float64{now}, history...)
	if err := store.Store(request.Context(), key, history, time.Duration(window)*time.Second); err != nil {
		return false, messageReadsError(c, err)
	}
	return true, nil
}
