package service

import (
	"context"
	"encoding/json"
	"math/big"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
	"golang.org/x/text/cases"
)

type ScopedResourceReader interface {
	Available(context.Context, domain.ResourceScope, string, []string) ([]string, error)
}
type AssociationUsernameReader interface {
	AssociationUsername(context.Context, string) (string, error)
}
type MedicalUploadReader interface {
	MedicalUploads(context.Context, string, []string) (map[string]string, error)
}
type ScopedRequestGuard struct {
	resources    ScopedResourceReader
	associations AssociationUsernameReader
	uploads      MedicalUploadReader
}

func NewScopedRequestGuard(resources ScopedResourceReader, associations AssociationUsernameReader, uploads MedicalUploadReader) *ScopedRequestGuard {
	return &ScopedRequestGuard{resources: resources, associations: associations, uploads: uploads}
}

var athleteOperations = map[string]bool{
	"manual_sections": true, "manual_asset": true,
	"profile_info": true, "profile_associates_course": true, "profile_associates_sport_association": true,
	"profile_settings_tables": true, "statistic_athlete_dashboard": true, "search_profile": true,
	"subscription_list": true, "subscription_list_all": true, "subscription_info": true,
	"subscription_payments": true, "subscription_card": true, "subscription_update": true,
	"subscription_add": true, "subscription_sign": true, "subscription_renew": true,
	"subscription_calculate_tax_code": true, "subscription_attendance": true, "subscription_calendar": true,
	"subscription_medical_certificate_upload": true, "subscription_medical_certificate_set_certificate_expiration": true,
	"subscription_medical_certificate_edit": true, "subscription_upload_document": true, "subscription_delete_document": true,
	"course_list": true, "course_overview_add": true, "course_overview_update": true, "course_overview_delete": true,
	"course_installment_make_payment": true, "payment_list": true, "carnet_list": true, "carnet_subscription_list": true,
	"stripe_pay": true, "stripe_multiple_pay": true, "retrieve_document": true, "medical_certificate_document": true,
	"attendance_day_mark_absent": true, "carnet_subscription_topup": true,
	"document_subscription": true, "document_subscription_view": true, "document_invoice": true, "document_invoice_view": true,
	"document_subscription_preview": true,
}
var requestResourceKinds = map[string]bool{
	"subscription": true, "course": true, "associate": true, "payment": true,
	"sport_association": true, "invoice": true, "course_subscription": true,
	"course_subscription_installment": true, "carnet": true, "carnet_subscription": true,
	"attendance_day": true, "group": true, "tag": true,
}

const unavailableResource = "Risorsa non disponibile per questo utente e associazione."
const unavailableMedical = "Certificato non disponibile per questa associazione."

type referenceCheck struct {
	kind, id, raw string
	err           error
}

// Check runs before collaborator-owner mapping and route grants. It validates
// referenced IDs only; returned querysets, writes and private document access
// still require their endpoint's complete resource/transaction invariants.
func (s *ScopedRequestGuard) Check(ctx context.Context, identity domain.ResolvedIdentity, request domain.IdentityRequest) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if identity.ImpersonationAssociationID == "" {
		return nil
	}
	if identity.Effective == nil || request.References == nil || request.References.Operation == "" {
		return domain.ErrInvalidScopedRequest
	}
	input := request.References
	name := input.Operation
	if identity.Effective.Role == domain.RoleAthlete && !athleteOperations[name] {
		return denial("Operazione non disponibile nel contesto dell’associazione.")
	}
	if name == "profile_update_password" || name == "oauth2_delete_account" || name == "profile_integrations" {
		return denial("Torna al tuo account per gestire le credenziali.")
	}
	scope := domain.ResourceScope{AssociationID: identity.ImpersonationAssociationID, EffectiveID: identity.Effective.ID, Role: identity.Effective.Role}
	// GroupAwareManager catches a malformed UUID and falls back to its unfiltered
	// group queryset. The mandatory association/family predicates still apply.
	if id, _, err := referenceUUID(request.GroupID); err == nil {
		scope.GroupID = id
	}
	username, usernameRead := "", false
	ownerUsername := func() (string, error) {
		if !usernameRead {
			value, err := s.associations.AssociationUsername(ctx, scope.AssociationID)
			if err != nil {
				return "", err
			}
			username, usernameRead = value, true
		}
		return username, nil
	}
	if name == "search_profile" {
		owner, err := ownerUsername()
		if err != nil {
			return err
		}
		value, _ := input.Path.Lookup("username")
		candidate, _ := value.(string)
		if cases.Fold().String(candidate) != cases.Fold().String(owner) {
			return denial("Associazione non disponibile.")
		}
	}
	if name == "subscription_add" {
		owner, err := ownerUsername()
		if err != nil {
			return err
		}
		object, ok := input.Body.(domain.RequestObject)
		if !ok {
			return domain.ErrInvalidScopedRequest
		}
		value, _ := object.Lookup("sport_association")
		candidate, _ := value.(string)
		if candidate != scope.AssociationID && candidate != owner {
			return denial("Seleziona la tua associazione.")
		}
	}
	var plan []referenceCheck
	stop := func(err error) bool { plan = append(plan, referenceCheck{err: err}); return false }
	var check func(string, any, int) bool
	check = func(key string, value any, depth int) bool {
		if depth > 1000 {
			return stop(domain.ErrInvalidScopedRequest)
		}
		switch key {
		case "payments":
			key = "payment"
		case "subscriptions":
			key = "subscription"
		case "courses":
			key = "course"
		case "tags":
			key = "tag"
		}
		key = strings.TrimSuffix(key, "_id")
		_, object := value.(domain.RequestObject)
		if key == "medical" && truthyReference(value) && !object {
			id, raw, err := referenceUUID(value)
			if err != nil {
				if err == domain.ErrInvalidScopedRequest {
					return stop(err)
				}
				return stop(denial("Certificato non disponibile."))
			}
			plan = append(plan, referenceCheck{kind: key, id: id, raw: raw})
			return true
		}
		if !requestResourceKinds[key] || value == nil {
			return true
		}
		if v, ok := value.(string); ok && v == "" {
			return true
		}
		if _, ok := value.(bool); ok {
			return true
		}
		if object, ok := value.(domain.RequestObject); ok {
			var exists bool
			value, exists = object.Lookup(key + "_id")
			if !exists {
				value, exists = object.Lookup("value")
			}
			if !exists {
				value, _ = object.Lookup("id")
			}
			if value == nil {
				return true
			}
		}
		if values, ok := value.([]any); ok {
			for _, v := range values {
				if !check(key, v, depth+1) {
					return false
				}
			}
			return true
		}
		if key == "sport_association" {
			owner, err := ownerUsername()
			if err != nil {
				return stop(err)
			}
			if rawReference(value) == owner {
				return true
			}
		}
		id, raw, err := referenceUUID(value)
		if err != nil {
			if err == domain.ErrInvalidScopedRequest {
				return stop(err)
			}
			return stop(denial(unavailableResource))
		}
		plan = append(plan, referenceCheck{kind: key, id: id, raw: raw})
		return true
	}
	var walk func(any, int) bool
	walk = func(value any, depth int) bool {
		if depth > 1000 {
			return stop(domain.ErrInvalidScopedRequest)
		}
		switch value := value.(type) {
		case domain.RequestObject:
			for _, field := range value {
				if object, ok := field.Value.(domain.RequestObject); ok && field.Name == "new_member_info" {
					role, exists := object.Lookup("role")
					if exists && !athleteRoleValue(role) {
						return stop(denial("Puoi creare solo account atleta."))
					}
				}
				if !check(field.Name, field.Value, depth+1) || !walk(field.Value, depth+1) {
					return false
				}
			}
		case []any:
			for _, v := range value {
				if !walk(v, depth+1) {
					return false
				}
			}
		case nil, string, bool, json.Number:
		default:
			return stop(domain.ErrInvalidScopedRequest)
		}
		return true
	}
	proceed := true
	for _, field := range input.Path {
		key := field.Name
		if key == "uid_subscription" || key == "subscriptionId" {
			key = "subscription"
		} else if key == "uid" {
			key = uidResource(name)
		}
		if !check(key, field.Value, 0) {
			proceed = false
			break
		}
	}
	if proceed {
		proceed = walk(input.Query, 0)
	}
	if proceed && request.Method != "GET" && request.Method != "HEAD" && request.Method != "OPTIONS" {
		walk(input.Body, 0)
	}
	return s.checkPlan(ctx, scope, identity.SessionID, plan)
}

func uidResource(name string) string {
	switch {
	case strings.HasPrefix(name, "subscription_"), strings.HasPrefix(name, "document_subscription"):
		return "subscription"
	case strings.HasPrefix(name, "document_invoice"):
		return "invoice"
	case name == "attendance_day_mark_absent":
		return "attendance_day"
	case name == "carnet_subscription_topup":
		return "carnet_subscription"
	case strings.HasPrefix(name, "course_installment_"):
		return "course_subscription_installment"
	case strings.HasPrefix(name, "course_"), name == "profile_associates_course":
		return "course"
	case name == "profile_associates_sport_association":
		return "sport_association"
	default:
		return ""
	}
}

func rawReference(value any) string {
	switch value := value.(type) {
	case string:
		return value
	case json.Number:
		return value.String()
	case bool:
		if value {
			return "True"
		}
		return "False"
	default:
		return ""
	}
}

// Django UUIDField uses UUID(int=value) for integers, UUID(hex=value) otherwise.
// Keep 128-bit numbers exact, and Python's accepted hex spelling, not a broader
// network GUID parser. Unsupported structured/float values are parsing failures.
func referenceUUID(value any) (string, string, error) {
	raw := rawReference(value)
	var id uuid.UUID
	switch value := value.(type) {
	case string:
		hex := strings.ReplaceAll(strings.ReplaceAll(value, "urn:", ""), "uuid:", "")
		hex = strings.ReplaceAll(strings.Trim(hex, "{}"), "-", "")
		if len(hex) != 32 {
			return "", raw, domain.ErrInvalidResourceBatch
		}
		parsed, err := uuid.Parse(hex)
		if err != nil {
			return "", raw, domain.ErrInvalidResourceBatch
		}
		id = parsed
	case bool:
		if value {
			id[15] = 1
		}
	case json.Number:
		if strings.ContainsAny(string(value), ".eE") {
			return "", raw, domain.ErrInvalidScopedRequest
		}
		// A 128-bit unsigned integer has at most 39 decimal digits. Do not
		// allocate an arbitrary-precision integer for unbounded request input.
		if len(value) > 39 {
			return "", raw, domain.ErrInvalidResourceBatch
		}
		number, ok := new(big.Int).SetString(string(value), 10)
		if !ok || number.Sign() < 0 || number.BitLen() > 128 {
			return "", raw, domain.ErrInvalidResourceBatch
		}
		number.FillBytes(id[:])
	default:
		return "", raw, domain.ErrInvalidScopedRequest
	}
	return id.String(), raw, nil
}
func athleteRoleValue(value any) bool {
	if value == "2" {
		return true
	}
	if number, ok := value.(json.Number); ok {
		// Role values are not monetary amounts. Python JSON floats use binary64;
		// parsing here also bounds huge exponents without allocating big powers.
		parsed, err := strconv.ParseFloat(string(number), 64)
		return err == nil && parsed == 2
	}
	return false
}
func truthyReference(value any) bool {
	switch value := value.(type) {
	case nil:
		return false
	case bool:
		return value
	case string:
		return value != ""
	case json.Number:
		parsed, err := strconv.ParseFloat(string(value), 64)
		if err != nil && parsed == 0 {
			return false
		} // Python float underflow is zero
		return parsed != 0
	case []any:
		return len(value) > 0
	case domain.RequestObject:
		return len(value) > 0
	default:
		return true
	}
}

// Fetch at most 512 distinct references of a kind at once and evaluate failures
// in original order. Alternating resource kinds do not cause per-row SQL/Redis
// calls. A later syntax/role failure cannot mask an earlier unavailable record.
func (s *ScopedRequestGuard) checkPlan(ctx context.Context, scope domain.ResourceScope, session string, plan []referenceCheck) error {
	type bucket struct {
		checks  []referenceCheck
		seen    map[string]bool
		results map[string]bool
		offset  int
	}
	buckets := map[string]*bucket{}
	key := func(c referenceCheck) string {
		if c.kind == "medical" {
			return c.raw
		}
		return c.id
	}
	for _, check := range plan {
		if check.err != nil {
			continue
		}
		b := buckets[check.kind]
		if b == nil {
			b = &bucket{seen: map[string]bool{}, results: map[string]bool{}}
			buckets[check.kind] = b
		}
		k := key(check)
		if !b.seen[k] {
			b.seen[k] = true
			b.checks = append(b.checks, check)
		}
	}
	for _, check := range plan {
		if err := ctx.Err(); err != nil {
			return err
		}
		if check.err != nil {
			return check.err
		}
		b := buckets[check.kind]
		k := key(check)
		if _, loaded := b.results[k]; !loaded {
			end := min(b.offset+512, len(b.checks))
			batch := b.checks[b.offset:end]
			b.offset = end
			grants := map[string]string{}
			if check.kind == "medical" {
				ids := make([]string, len(batch))
				for i, c := range batch {
					ids[i] = c.raw
				}
				var err error
				grants, err = s.uploads.MedicalUploads(ctx, session, ids)
				if err != nil {
					return err
				}
			}
			var ids []string
			for _, c := range batch {
				if grants[c.raw] != scope.AssociationID {
					ids = append(ids, c.id)
				}
			}
			visible := map[string]bool{}
			if len(ids) > 0 {
				available, err := s.resources.Available(ctx, scope, check.kind, ids)
				if err != nil {
					return err
				}
				for _, id := range available {
					visible[id] = true
				}
			}
			for _, c := range batch {
				b.results[key(c)] = visible[c.id] || grants[c.raw] == scope.AssociationID
			}
		}
		if !b.results[k] {
			if check.kind == "medical" {
				return denial(unavailableMedical)
			}
			return denial(unavailableResource)
		}
	}
	return nil
}
