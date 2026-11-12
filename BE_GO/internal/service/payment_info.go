package service

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

var ErrPaymentInfoNotFound = errors.New("payment information not found")
var errPaymentInfoRelationship = errors.New("invalid persisted payment information relationship")

type PaymentInfoUUIDError struct{ Detail string }

func (e *PaymentInfoUUIDError) Error() string { return e.Detail }

type PaymentInfo struct{ reader domain.PaymentInfoReader }

func NewPaymentInfo(reader domain.PaymentInfoReader) *PaymentInfo {
	return &PaymentInfo{reader: reader}
}

// Read preserves public UUID-wide checkout. Only resolved impersonation scope
// constrains the association; ordinary actor ownership must not be invented.
func (s *PaymentInfo) Read(ctx context.Context, identity domain.ResolvedIdentity, identifier, group string) (map[string]any, error) {
	if err := ctx.Err(); err != nil {
		return nil, err
	}
	id, err := paymentInfoParseUUID(identifier)
	if err != nil {
		return nil, err
	}
	lookup := domain.PaymentInfoLookup{PaymentID: id}
	if identity.ImpersonationAssociationID != "" {
		association, err := uuid.Parse(identity.ImpersonationAssociationID)
		if err != nil {
			return nil, domain.ErrInvalidScopedRequest
		}
		lookup.AssociationID = &association
	}
	// GroupAwareManager ignores malformed group UUIDs, including anonymous reads.
	if value, err := paymentInfoParseUUID(group); err == nil {
		lookup.GroupID = &value
	}
	row, err := s.reader.ReadPaymentInfo(ctx, lookup)
	if err != nil {
		return nil, err
	}
	if row == nil {
		return nil, ErrPaymentInfoNotFound
	}
	if row.Association == nil || row.Association.OwnerID == nil {
		return nil, errPaymentInfoRelationship
	}
	course, err := paymentInfoJSON(row.Course)
	if err != nil {
		return nil, err
	}
	var at any
	if row.CreatedAt != nil {
		value := row.CreatedAt.UTC()
		text := value.Format("2006-01-02T15:04:05")
		if micro := value.Nanosecond() / 1000; micro != 0 {
			text += fmt.Sprintf(".%06d", micro)
		}
		at = text + "Z"
	}
	var user, associate any
	if row.User != nil {
		u := row.User
		user = map[string]any{"first_name": u.FirstName, "last_name": u.LastName, "email": u.Email, "phone": u.Phone, "username": u.Username, "role": u.Role}
	}
	if row.Associate != nil {
		a := row.Associate
		associate = map[string]any{"first_name": a.FirstName, "last_name": a.LastName, "tax_code": a.TaxCode}
	}
	data := map[string]any{"user": user, "associate": associate, "subject": row.Subject, "amount": row.Amount,
		"sport_association": row.Association.ID.String(), "creation_date": at, "course": course}
	var info map[string]any
	switch row.Subject {
	case 0:
		info = paymentInfoAssociation(row.Association)
		meta, err := paymentInfoJSON(row.Meta)
		if err != nil {
			return nil, err
		}
		if paymentInfoTruthy(meta) {
			meta, err = s.metadata(ctx, meta)
			if err != nil {
				return nil, err
			}
		}
		info["meta_payment_categories"] = meta
	case 1:
		info = paymentInfoAssociation(row.Association)
	case 2:
		relation := row.Direct
		if relation == nil {
			relation = row.Installment
		}
		if relation != nil {
			if relation.Offering == nil {
				return nil, errPaymentInfoRelationship
			}
			o := relation.Offering
			var association any
			if o.Association != nil {
				association = paymentInfoAssociation(o.Association)
			}
			info = map[string]any{"title": o.Title, "description": o.Description, "fee": o.Fee, "sport_association": association}
		} else {
			if row.Carnet == nil || row.Carnet.Offering == nil {
				return nil, errPaymentInfoRelationship
			}
			o := row.Carnet.Offering
			title := "None"
			if o.Title != nil {
				title = *o.Title
			}
			var fee any
			if o.Fee != nil {
				number := json.Number(*o.Fee)
				// PostgreSQL numeric can hold NaN. Django's raw carnet Decimal
				// response fails JSON rendering; enter the same HTML error path
				// before Echo's encoder can turn it into a generic JSON 500.
				if _, err := json.Marshal(number); err != nil {
					return nil, err
				}
				fee = number
			}
			association := paymentInfoAssociation(o.Association)
			if o.Association == nil {
				association = map[string]any{"denomination": "", "tax_code": "", "checkout_info": ""}
			}
			data["info"] = map[string]any{"title": "Carnet (" + title + ")", "description": o.Description, "fee": fee, "sport_association": association}
			return data, ctx.Err() // Existing carnet response returns before category enrichment.
		}
	default:
		return nil, errPaymentInfoRelationship
	}
	info["payment_category_name"] = row.CategoryName
	data["info"] = info
	return data, ctx.Err()
}

func paymentInfoAssociation(row *domain.PaymentInfoAssociation) map[string]any {
	if row == nil {
		return nil
	}
	return map[string]any{"denomination": row.Denomination, "tax_code": row.TaxCode, "checkout_info": row.CheckoutInfo}
}
func paymentInfoJSON(raw json.RawMessage) (any, error) {
	if len(raw) == 0 {
		return nil, nil
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var result any
	if err := decoder.Decode(&result); err != nil {
		return nil, err
	}
	return paymentInfoJSONFractions(result)
}

// Django's JSONField decoder retains arbitrary-precision JSON integers but
// reads fractional tokens as binary64. Match that transport behavior without
// applying float conversion to Decimal amounts, fees or integer identifiers.
func paymentInfoJSONFractions(value any) (any, error) {
	switch v := value.(type) {
	case json.Number:
		if !strings.ContainsAny(string(v), ".eE") {
			return v, nil
		}
		number, err := strconv.ParseFloat(string(v), 64)
		if math.IsInf(number, 0) || math.IsNaN(number) {
			return nil, errPaymentInfoRelationship
		}
		if err != nil && number != 0 {
			return nil, err
		}
		return number, nil
	case []any:
		for i, item := range v {
			converted, err := paymentInfoJSONFractions(item)
			if err != nil {
				return nil, err
			}
			v[i] = converted
		}
	case map[string]any:
		for key, item := range v {
			converted, err := paymentInfoJSONFractions(item)
			if err != nil {
				return nil, err
			}
			v[key] = converted
		}
	}
	return value, nil
}
func paymentInfoTruthy(value any) bool {
	switch v := value.(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case json.Number:
		n, _ := strconv.ParseFloat(string(v), 64)
		return n != 0
	case float64:
		return v != 0
	case []any:
		return len(v) > 0
	case map[string]any:
		return len(v) > 0
	default:
		return true
	}
}
func (s *PaymentInfo) metadata(ctx context.Context, value any) (any, error) {
	entries, ok := value.([]any)
	if !ok {
		return nil, errPaymentInfoRelationship
	}
	ids := make([]uuid.UUID, 0, len(entries))
	positions := make([]uuid.UUID, len(entries))
	seen := map[uuid.UUID]bool{}
	for i, entry := range entries {
		item, ok := entry.(map[string]any)
		if !ok {
			return nil, errPaymentInfoRelationship
		}
		raw, present := item["payment_category_id"]
		if !present || raw == nil {
			return nil, errPaymentInfoRelationship
		}
		text, _, err := referenceUUID(raw)
		if value, ok := raw.(string); ok {
			parsed, parseErr := paymentInfoParseUUID(value)
			text, err = parsed.String(), parseErr
		}
		if err != nil {
			return nil, errPaymentInfoRelationship
		}
		id := uuid.MustParse(text)
		positions[i] = id
		if !seen[id] {
			ids = append(ids, id)
			seen[id] = true
		}
	}
	names, err := s.reader.ReadPaymentInfoCategoryNames(ctx, ids)
	if err != nil {
		return nil, err
	}
	for i, entry := range entries {
		name, ok := names[positions[i]]
		if !ok {
			return nil, errPaymentInfoRelationship
		}
		entry.(map[string]any)["payment_category_name"] = name
	}
	return entries, nil
}
