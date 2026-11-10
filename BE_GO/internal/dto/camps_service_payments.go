package dto

import (
	"encoding/json"
	"math"
	"math/big"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// ErrCampsPaymentNotPorted refuses a payment walk whose Python failure this
// port does not reproduce (a database rejection of the computed amount or
// notes) before any write.
var ErrCampsPaymentNotPorted = refusal.New("camps payment walk outcome is not ported")

// CampsServiceMeta is what camps_and_retreats_periods_services_update writes
// into each matching meta_payment_categories item.
type CampsServiceMeta struct {
	ServiceID string
	// Amount is float(serializer.data['fee']).
	Amount   float64
	Title    *string
	Category string
}

// CampsPaymentStep is one Payment.save() of the walk: the metas after the
// matching item was updated, or the final amount and notes.
type CampsPaymentStep struct {
	Meta   any
	Final  bool
	Amount float64
	Notes  string
}

// CampsPaymentWalk is the view's loop for one subscription period: the saves in
// order, then the exception that stops the request (nil when it completes).
type CampsPaymentWalk struct {
	Steps   []CampsPaymentStep
	Failure error
}

// CampsWalkPayment runs the loop over one payment's metas (parsed JSON, nil
// for SQL NULL) with the period title and fee and the camp title and
// description.
func CampsWalkPayment(metas any, update CampsServiceMeta, periodTitle *string, periodFee string, campTitle, campDescription *string) CampsPaymentWalk {
	var walk CampsPaymentWalk
	items, failure := campsIterate(metas)
	if failure != nil {
		walk.Failure = failure
		return walk
	}
	for index, item := range items {
		object, ok := item.(domain.RequestObject)
		if !ok {
			walk.Failure = campsSubscriptError(item)
			return walk
		}
		value, present := object.Lookup("camps_and_retreats_period_service_id")
		if !present {
			walk.Failure = &CampsPythonError{Message: "'camps_and_retreats_period_service_id'"}
			return walk
		}
		if value != update.ServiceID {
			continue
		}
		object = campsSet(object, "amount", json.Number(pythonFloatRepr(update.Amount)))
		var title any
		if update.Title != nil {
			title = *update.Title
		}
		object = campsSet(object, "title", title)
		object = campsSet(object, "payment_category_id", update.Category)
		items[index] = object
		metas = campsReplace(metas, items)
		walk.Steps = append(walk.Steps, CampsPaymentStep{Meta: metas})
	}
	notes := "Iscrizione al periodo " + campsOptional(periodTitle) + " del campo " + campsOptional(campTitle)
	notes += "\n" + campsOptional(campDescription)
	notes += "\n"
	amount := campsNumber{integer: new(big.Int)}
	for _, item := range items {
		object := item.(domain.RequestObject)
		title, present := object.Lookup("title")
		if !present {
			walk.Failure = &CampsPythonError{Message: "'title'"}
			return walk
		}
		value, present := object.Lookup("amount")
		if !present {
			walk.Failure = &CampsPythonError{Message: "'amount'"}
			return walk
		}
		notes += "\n" + PythonStr(title) + " - " + PythonStr(value) + "€"
		if amount, failure = amount.add(value); failure != nil {
			walk.Failure = failure
			return walk
		}
	}
	fee, _ := strconv.ParseFloat(periodFee, 64)
	total, failure := amount.add(json.Number(pythonFloatRepr(fee)))
	if failure != nil {
		walk.Failure = failure
		return walk
	}
	walk.Steps = append(walk.Steps, CampsPaymentStep{Final: true, Amount: total.float, Notes: notes})
	return walk
}

func campsOptional(value *string) string {
	if value == nil {
		return "None"
	}
	return *value
}

// campsIterate is `for item in metas` over a parsed JSON value.
func campsIterate(metas any) ([]any, error) {
	switch value := metas.(type) {
	case []any:
		return append([]any(nil), value...), nil
	case domain.RequestObject:
		items := make([]any, 0, len(value))
		for _, field := range value {
			items = append(items, field.Name)
		}
		return items, nil
	case string:
		items := []any{}
		for _, r := range pythonCodePoints(value) {
			items = append(items, string(r))
		}
		return items, nil
	}
	return nil, &CampsPythonError{Message: "'" + pythonTypeName(metas) + "' object is not iterable"}
}

// campsSubscriptError is item['camps_and_retreats_period_service_id'] on a
// non-dict item.
func campsSubscriptError(item any) error {
	switch item.(type) {
	case []any:
		return &CampsPythonError{Message: "list indices must be integers or slices, not str"}
	case string:
		return &CampsPythonError{Message: "string indices must be integers, not 'str'"}
	}
	return &CampsPythonError{Message: "'" + pythonTypeName(item) + "' object is not subscriptable"}
}

func campsSet(object domain.RequestObject, name string, value any) domain.RequestObject {
	copied := append(domain.RequestObject(nil), object...)
	for index := range copied {
		if copied[index].Name == name {
			copied[index].Value = value
			return copied
		}
	}
	return append(copied, domain.RequestField{Name: name, Value: value})
}

// campsReplace rebuilds the metas container with the updated items (only a
// list holds dict items).
func campsReplace(metas any, items []any) any {
	if _, ok := metas.([]any); ok {
		return append([]any(nil), items...)
	}
	return metas
}

// campsNumber is the Python int or float accumulated by `amount += ...`.
type campsNumber struct {
	isFloat bool
	integer *big.Int
	float   float64
}

func (n campsNumber) typeName() string {
	if n.isFloat {
		return "float"
	}
	return "int"
}

func (n campsNumber) asFloat() (float64, error) {
	if n.isFloat {
		return n.float, nil
	}
	value, _ := new(big.Float).SetInt(n.integer).Float64()
	if math.IsInf(value, 0) {
		return 0, &CampsPythonError{Message: "int too large to convert to float"}
	}
	return value, nil
}

func (n campsNumber) add(value any) (campsNumber, error) {
	var other campsNumber
	switch v := value.(type) {
	case bool:
		other = campsNumber{integer: big.NewInt(map[bool]int64{true: 1, false: 0}[v])}
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			parsed, _ := strconv.ParseFloat(string(v), 64)
			other = campsNumber{isFloat: true, float: parsed}
		} else {
			parsed, _ := new(big.Int).SetString(string(v), 10)
			other = campsNumber{integer: parsed}
		}
	default:
		return n, &CampsPythonError{Message: "unsupported operand type(s) for +=: '" + n.typeName() + "' and '" + pythonTypeName(value) + "'"}
	}
	if !n.isFloat && !other.isFloat {
		return campsNumber{integer: new(big.Int).Add(n.integer, other.integer)}, nil
	}
	left, err := n.asFloat()
	if err != nil {
		return n, err
	}
	right, err := other.asFloat()
	if err != nil {
		return n, err
	}
	return campsNumber{isFloat: true, float: left + right}, nil
}

// CampsStoredAmount is DecimalField(9, 2) storage of a float: Django's
// create_decimal_from_float with nine significant digits, then PostgreSQL's
// numeric(9,2) rounding (half away from zero). ok is false when PostgreSQL
// would reject the value (overflow) or the amount check constraint fails.
func CampsStoredAmount(value float64) (string, string, bool) {
	if math.IsInf(value, 0) || math.IsNaN(value) {
		return "", "", false
	}
	nine := strconv.FormatFloat(value, 'e', 8, 64)
	rational, ok := new(big.Rat).SetString(nine)
	if !ok {
		return "", "", false
	}
	scaled := new(big.Rat).Mul(rational, big.NewRat(100, 1))
	negative := scaled.Sign() < 0
	scaled.Abs(scaled)
	quotient, remainder := new(big.Int).QuoRem(scaled.Num(), scaled.Denom(), new(big.Int))
	if new(big.Int).Mul(remainder, big.NewInt(2)).Cmp(scaled.Denom()) >= 0 {
		quotient.Add(quotient, big.NewInt(1))
	}
	if quotient.Cmp(big.NewInt(1_000_000_000)) >= 0 {
		return "", "", false
	}
	digits := quotient.String()
	for len(digits) < 3 {
		digits = "0" + digits
	}
	stored := digits[:len(digits)-2] + "." + digits[len(digits)-2:]
	if negative && quotient.Sign() != 0 {
		return "", "", false
	}
	return nine, stored, true
}

// CampsJSONText is json.dumps of the metas Django writes to the JSONField.
func CampsJSONText(value any) string { return string(PythonJSONDumps(value)) }

// CampsJSONAudit is auditlog's json.dumps(value, sort_keys=True).
func CampsJSONAudit(value any) string {
	if value == nil {
		return "null"
	}
	return string(PythonJSONDumpsSorted(value))
}

// CampsFloat is Python's repr() of a float.
func CampsFloat(value float64) string { return pythonFloatRepr(value) }
