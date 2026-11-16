package service

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"math"
	"math/big"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/stripeapi"
)

// PaymentStatsStripe is payment_stats' stripe_charges block when
// stripe_direct_credentials_configured(): the cached fees of the range, or
// stripe.BalanceTransaction.list(...).auto_paging_iter() summed and cached for
// 300 seconds; any exception inside the view's try answers zero charges, uncached.
type PaymentStatsStripe struct {
	routes *StripeRoutes
	cache  domain.PaymentStatsStripeCache
}

func NewPaymentStatsStripe(routes *StripeRoutes, cache domain.PaymentStatsStripeCache) *PaymentStatsStripe {
	return &PaymentStatsStripe{routes: routes, cache: cache}
}

// WithStripeFees lets payment_stats compute the Stripe fees when Stripe is configured.
func (s *PaymentReads) WithStripeFees(fees *PaymentStatsStripe) *PaymentReads {
	s.fees = fees
	return s
}

// errPaymentStatsStripeZero is an exception the view's try/except turns into zero charges.
var errPaymentStatsStripeZero = errors.New("stripe fees exception")

// charges is the view's stripe_charges: nil means the initial {'total': 0, 'stripe_fee': 0}.
func (f *PaymentStatsStripe) charges(ctx context.Context, from, to time.Time) (*domain.PaymentStripeCharges, error) {
	configured, err := f.routes.availability.DirectCredentialsConfigured(ctx)
	if err != nil || !configured {
		return nil, err
	}
	key, err := f.routes.requestKey(ctx)
	if err != nil {
		return nil, err
	}
	digest := sha256.Sum256([]byte(key))
	start, end := from.Unix(), to.Unix()
	cacheKey := "stripe_fees_direct_" + hex.EncodeToString(digest[:])[:24] + "_" + strconv.FormatInt(start, 10) + "_" +
		strconv.FormatInt(end, 10)
	cached, found, err := f.cache.StripeCharges(ctx, cacheKey)
	if err != nil {
		return nil, err
	}
	if found {
		return paymentStatsRenderable(&cached)
	}
	fee, err := f.fees(ctx, f.routes.client.Session(key), start, end)
	if errors.Is(err, errPaymentStatsStripeZero) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	charges := domain.PaymentStripeCharges{Total: fee, StripeFee: fee}
	if err := f.cache.StoreStripeCharges(ctx, cacheKey, charges); err != nil {
		// cache.set runs inside the view's try: the except answers zero charges.
		return nil, nil
	}
	return paymentStatsRenderable(&charges)
}

// errPaymentStatsNonFinite is DRF's JSONRenderer (STRICT_JSON) rendering a
// non-finite float in stripe_charges: json.dumps(allow_nan=False) raises
// ValueError while the response renders, which is the 500 page. The cache
// entry is already written, as in Django.
var errPaymentStatsNonFinite = errors.New("payment_stats: out of range float values are not JSON compliant")

// paymentStatsRenderable answers charges the view can render: a sum of
// float amounts can reach an infinity or NaN (json.loads reads 1e400 and NaN).
func paymentStatsRenderable(charges *domain.PaymentStripeCharges) (*domain.PaymentStripeCharges, error) {
	for _, value := range []float64{charges.Total, charges.StripeFee} {
		if math.IsInf(value, 0) || math.IsNaN(value) {
			return nil, errPaymentStatsNonFinite
		}
	}
	return charges, nil
}

// paymentStatsItems is iterating a list object's data (getattr(self, 'data', [])): a
// non-list value raises on its first element (or is not iterable) unless it is empty.
func paymentStatsItems(value any, present bool) ([]any, bool) {
	if !present {
		return nil, true
	}
	switch v := value.(type) {
	case []any:
		return v, true
	case domain.RequestObject:
		return nil, len(v) == 0
	case string:
		return nil, v == ""
	}
	return nil, false
}

// paymentStatsSum is the Python sum of the fee amounts: int of any size until a
// float joins (int + float converts the int with float(), which raises
// OverflowError beyond the float range), then a float.
type paymentStatsSum struct {
	float   bool
	integer *big.Int
	number  float64
}

func newPaymentStatsSum() paymentStatsSum { return paymentStatsSum{integer: new(big.Int)} }

// add is += for a parsed JSON value; false is the TypeError or OverflowError
// the view's except turns into zero charges.
func (s *paymentStatsSum) add(value any) bool {
	switch v := value.(type) {
	case bool:
		number := int64(0)
		if v {
			number = 1
		}
		return s.addInt(big.NewInt(number))
	case json.Number:
		if !strings.ContainsAny(string(v), ".eE") {
			if number, ok := new(big.Int).SetString(string(v), 10); ok {
				return s.addInt(number)
			}
		}
		number, ok := parsePythonFloat(string(v))
		return ok && s.addFloat(number)
	case float64:
		// A JSON reader that yields float64 (NaN and infinities included).
		return s.addFloat(v)
	case int64:
		return s.addInt(big.NewInt(v))
	case int:
		return s.addInt(big.NewInt(int64(v)))
	case *big.Int:
		return s.addInt(v)
	}
	// str, None, list and dict raise a TypeError.
	return false
}

func (s *paymentStatsSum) addInt(value *big.Int) bool {
	if !s.float {
		s.integer.Add(s.integer, value)
		return true
	}
	number, ok := pythonIntFloat(value)
	if !ok {
		return false
	}
	s.number += number
	return true
}

func (s *paymentStatsSum) addFloat(value float64) bool {
	if s.float {
		s.number += value
		return true
	}
	number, ok := pythonIntFloat(s.integer)
	if !ok {
		return false
	}
	s.float, s.number = true, number+value
	return true
}

// charge is round(float(total) / 100, 2); float(total) of a huge int raises
// OverflowError, and round keeps NaN and infinities (json.dumps then writes
// NaN and Infinity).
func (s paymentStatsSum) charge() (float64, bool) {
	number := s.number
	if !s.float {
		var ok bool
		if number, ok = pythonIntFloat(s.integer); !ok {
			return 0, false
		}
	}
	return pythonRound2(number / 100), true
}

// pythonIntFloat is float(int): the correctly rounded float64, false where
// Python raises OverflowError.
func pythonIntFloat(value *big.Int) (float64, bool) {
	number, _ := new(big.Float).SetInt(value).Float64()
	return number, !math.IsInf(number, 0)
}

// parsePythonFloat is float() of a JSON number token, and json.loads' NaN and
// Infinity literals.
func parsePythonFloat(text string) (float64, bool) {
	switch text {
	case "NaN":
		return math.NaN(), true
	case "Infinity":
		return math.Inf(1), true
	case "-Infinity":
		return math.Inf(-1), true
	}
	number, err := strconv.ParseFloat(text, 64)
	// A decimal exponent beyond the float range is float()'s infinity or zero,
	// not an error.
	return number, err == nil || errors.Is(err, strconv.ErrRange)
}

// fees sums the stripe_fee amounts of every non-payout balance transaction
// (the view adds to total only inside the stripe_fee branch, so both sums are equal)
// and returns round(float(total) / 100, 2).
func (f *PaymentStatsStripe) fees(ctx context.Context, session *stripeapi.Session, start, end int64) (float64, error) {
	zero := errPaymentStatsStripeZero
	created := domain.RequestObject{{Name: "gte", Value: json.Number(strconv.FormatInt(start, 10))},
		{Name: "lte", Value: json.Number(strconv.FormatInt(end, 10))}}
	page, err := session.List(ctx, "/v1/balance_transactions", []stripeapi.Param{{Key: "created", Value: created}})
	if err != nil {
		return 0, zero
	}
	total := newPaymentStatsSum()
	for {
		items, ok := paymentStatsItems(page.Lookup("data"))
		if !ok {
			return 0, zero
		}
		for _, item := range items {
			txn, ok := item.(domain.RequestObject)
			if !ok {
				return 0, zero
			}
			kind, present := txn.Lookup("type")
			if !present {
				return 0, zero
			}
			if kind == "payout" {
				continue
			}
			details, present := txn.Lookup("fee_details")
			if !present {
				return 0, zero
			}
			list, ok := paymentStatsItems(details, true)
			if !ok {
				return 0, zero
			}
			for _, entry := range list {
				detail, ok := entry.(domain.RequestObject)
				if !ok {
					return 0, zero
				}
				detailType, present := detail.Lookup("type")
				if !present {
					return 0, zero
				}
				if detailType != "stripe_fee" {
					continue
				}
				amount, present := detail.Lookup("amount")
				if !present || !total.add(amount) {
					return 0, zero
				}
			}
		}
		more, _ := page.Lookup("has_more")
		if !dto.PythonTruthy(more) {
			charge, ok := total.charge()
			if !ok {
				return 0, zero
			}
			return charge, nil
		}
		// next_page: list(**_retrieve_params, starting_after=data[-1].id) on the list's url.
		// The SDK encodes whatever Python value the id is (str(int), str(bool),
		// a dict's nested keys) and the request's outcome decides.
		if len(items) == 0 {
			return 0, zero
		}
		last, _ := items[len(items)-1].(domain.RequestObject)
		id, present := last.Lookup("id")
		if !present || !dto.PythonTruthy(id) {
			return 0, zero
		}
		url, _ := page.Lookup("url")
		path, isPath := url.(string)
		if !isPath {
			return 0, zero
		}
		page, err = session.List(ctx, path, []stripeapi.Param{{Key: "created", Value: created}, {Key: "starting_after", Value: id}})
		if err != nil {
			return 0, zero
		}
		// page.is_empty is `not self.data`.
		if value, _ := page.Lookup("data"); !dto.PythonTruthy(value) {
			charge, ok := total.charge()
			if !ok {
				return 0, zero
			}
			return charge, nil
		}
	}
}

// paymentStatsDecimal is decimal.Decimal(value) of a float: its exact binary value.
func paymentStatsDecimal(value float64) *big.Rat {
	return new(big.Rat).SetFloat64(value)
}

// paymentStatsContext rounds to the default decimal context (28 significant digits, ROUND_HALF_EVEN).
func paymentStatsContext(value *big.Rat) *big.Rat {
	if value.Sign() == 0 {
		return value
	}
	ten := new(big.Rat).SetInt64(10)
	low := new(big.Rat).SetInt(new(big.Int).Exp(big.NewInt(10), big.NewInt(27), nil))
	high := new(big.Rat).SetInt(new(big.Int).Exp(big.NewInt(10), big.NewInt(28), nil))
	// scaled = |value| * 10^k with 10^27 <= scaled < 10^28 (k may be negative).
	scaled := new(big.Rat).Abs(value)
	k := 0
	for scaled.Cmp(low) < 0 {
		scaled.Mul(scaled, ten)
		k++
	}
	for scaled.Cmp(high) >= 0 {
		scaled.Quo(scaled, ten)
		k--
	}
	quotient, remainder := new(big.Int).QuoRem(scaled.Num(), scaled.Denom(), new(big.Int))
	if c := new(big.Int).Mul(remainder, big.NewInt(2)).Cmp(scaled.Denom()); c > 0 || (c == 0 && quotient.Bit(0) == 1) {
		quotient.Add(quotient, big.NewInt(1))
	}
	result := new(big.Rat).SetInt(quotient)
	power := new(big.Rat).SetInt(new(big.Int).Exp(big.NewInt(10), big.NewInt(int64(max(k, -k))), nil))
	if k > 0 {
		result.Quo(result, power)
	} else if k < 0 {
		result.Mul(result, power)
	}
	if value.Sign() < 0 {
		result.Neg(result)
	}
	return result
}
