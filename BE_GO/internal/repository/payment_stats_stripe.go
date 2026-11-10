package repository

import (
	"context"
	"encoding/binary"
	"errors"
	"math"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/redis/go-redis/v9"
)

// PaymentStatsStripeCache is payment_stats' cache of the Stripe fees of a date
// range, stored as django-redis does on Python 3.13: key ':1:<cache key>', the
// value pickle.dumps({'total': float, 'stripe_fee': float}, 4), 300 seconds.
type PaymentStatsStripeCache struct{ client *redis.Client }

func NewPaymentStatsStripeCache(client *redis.Client) (*PaymentStatsStripeCache, error) {
	if client == nil || client.Options().DB != 1 {
		return nil, errors.New("payment stats Stripe cache requires Django cache database 1")
	}
	return &PaymentStatsStripeCache{client: client}, nil
}

// paymentStatsStripeTTL is cache.set(cache_key, stripe_charges, 300).
const paymentStatsStripeTTL = 300 * time.Second

// StripeCharges is cache.get(key): found, and the cached dict when it is the
// {'total': float, 'stripe_fee': float} payment_stats writes (anything else:
// ErrPaymentReadsNotPorted).
func (r *PaymentStatsStripeCache) StripeCharges(ctx context.Context, key string) (domain.PaymentStripeCharges, bool, error) {
	raw, err := r.client.Get(ctx, ":1:"+key).Bytes()
	if errors.Is(err, redis.Nil) {
		return domain.PaymentStripeCharges{}, false, nil
	}
	if err != nil {
		return domain.PaymentStripeCharges{}, false, err
	}
	value, err := unpickleDataRestoreProgress(raw)
	if err != nil {
		return domain.PaymentStripeCharges{}, true, domain.ErrPaymentReadsNotPorted
	}
	dict, ok := value.(*PickledDict)
	if !ok || len(dict.Keys) != 2 || dict.Keys[0] != "total" || dict.Keys[1] != "stripe_fee" {
		return domain.PaymentStripeCharges{}, true, domain.ErrPaymentReadsNotPorted
	}
	total, okTotal := dict.Values["total"].(float64)
	fee, okFee := dict.Values["stripe_fee"].(float64)
	if !okTotal || !okFee {
		return domain.PaymentStripeCharges{}, true, domain.ErrPaymentReadsNotPorted
	}
	return domain.PaymentStripeCharges{Total: total, StripeFee: fee}, true, nil
}

// StoreStripeCharges is cache.set(key, {'total': ..., 'stripe_fee': ...}, 300).
func (r *PaymentStatsStripeCache) StoreStripeCharges(ctx context.Context, key string, charges domain.PaymentStripeCharges) error {
	return r.client.Set(ctx, ":1:"+key, picklePaymentStripeCharges(charges), paymentStatsStripeTTL).Err()
}

// picklePaymentStripeCharges is pickle.dumps({'total': t, 'stripe_fee': f}, 4).
func picklePaymentStripeCharges(charges domain.PaymentStripeCharges) []byte {
	body := []byte{'}', 0x94, '('}
	for _, item := range []struct {
		key   string
		value float64
	}{{"total", charges.Total}, {"stripe_fee", charges.StripeFee}} {
		body = append(body, 0x8c, byte(len(item.key)))
		body = append(body, item.key...)
		body = append(body, 0x94, 'G')
		body = binary.BigEndian.AppendUint64(body, math.Float64bits(item.value))
	}
	body = append(body, 'u', '.')
	out := []byte{0x80, 4, 0x95}
	out = binary.LittleEndian.AppendUint64(out, uint64(len(body)))
	return append(out, body...)
}
