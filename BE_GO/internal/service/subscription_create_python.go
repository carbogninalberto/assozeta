package service

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"math/big"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// Python exceptions create_subscription raises, with their str() (the 400
// body of subscription/renew; subscription-renew.md).

func pythonFailure(text string) error { return &domain.SubscriptionCreateFailure{Text: text} }

func pythonKeyError(key string) error { return pythonFailure(dto.PythonStringRepr(key)) }

// pythonTypeLabel is type(value).__name__ for a parsed JSON value.
func pythonTypeLabel(value any) string {
	switch v := value.(type) {
	case nil:
		return "NoneType"
	case bool:
		return "bool"
	case string:
		return "str"
	case []any:
		return "list"
	case domain.RequestObject:
		return "dict"
	case json.Number:
		if containsAny(string(v), ".eE") {
			return "float"
		}
		return "int"
	}
	return "object"
}

func containsAny(text, chars string) bool {
	for _, r := range text {
		for _, c := range chars {
			if r == c {
				return true
			}
		}
	}
	return false
}

// pythonNumberValue is a Python number (an int, a float or a bool, which is an
// int) as Python compares it: an exact rational, an infinity, or NaN.
type pythonNumberValue struct {
	rational *big.Rat
	// infinite is -1 or 1 for the infinities; nan marks NaN.
	infinite int
	nan      bool
}

func pythonFloatNumber(value float64) pythonNumberValue {
	switch {
	case math.IsNaN(value):
		return pythonNumberValue{nan: true}
	case math.IsInf(value, 1):
		return pythonNumberValue{infinite: 1}
	case math.IsInf(value, -1):
		return pythonNumberValue{infinite: -1}
	}
	rational := new(big.Rat)
	rational.SetFloat64(value) // exact: a finite float is a binary fraction
	return pythonNumberValue{rational: rational}
}

// pythonNumericValue is value as a Python number; ok is false for the values
// number equality does not apply to (texts, containers, None).
func pythonNumericValue(value any) (pythonNumberValue, bool) {
	switch v := value.(type) {
	case bool:
		if v {
			return pythonNumberValue{rational: big.NewRat(1, 1)}, true
		}
		return pythonNumberValue{rational: big.NewRat(0, 1)}, true
	case float64:
		return pythonFloatNumber(v), true
	case json.Number:
		text := string(v)
		if !strings.ContainsAny(text, ".eE") {
			integer, ok := new(big.Int).SetString(text, 10)
			if !ok {
				return pythonNumberValue{}, false
			}
			return pythonNumberValue{rational: new(big.Rat).SetInt(integer)}, true
		}
		number, err := strconv.ParseFloat(text, 64)
		if err != nil {
			if failure, ok := err.(*strconv.NumError); !ok || failure.Err != strconv.ErrRange {
				return pythonNumberValue{}, false
			}
		}
		return pythonFloatNumber(number), true
	}
	return pythonNumberValue{}, false
}

func (n pythonNumberValue) equal(other pythonNumberValue) bool {
	if n.nan || other.nan {
		return false
	}
	if n.infinite != 0 || other.infinite != 0 {
		return n.infinite == other.infinite
	}
	return n.rational.Cmp(other.rational) == 0
}

// pythonJSONEqual is Python's == between two parsed JSON values (the request
// reader's model: domain.RequestObject objects): numbers compare by
// value across int, float and bool (an int exactly, a float as its binary
// value, NaN unequal to everything), texts and containers structurally (dict
// keys are unique in JSON, so same length and every key holds an equal value).
func pythonJSONEqual(left, right any) bool {
	if number, ok := pythonNumericValue(left); ok {
		other, ok := pythonNumericValue(right)
		return ok && number.equal(other)
	}
	switch l := left.(type) {
	case nil:
		return right == nil
	case string:
		r, ok := right.(string)
		return ok && l == r
	case []any:
		r, ok := right.([]any)
		if !ok || len(l) != len(r) {
			return false
		}
		for index := range l {
			if !pythonJSONEqual(l[index], r[index]) {
				return false
			}
		}
		return true
	case domain.RequestObject:
		r, ok := right.(domain.RequestObject)
		if !ok || len(l) != len(r) {
			return false
		}
		for _, field := range l {
			value, present := r.Lookup(field.Name)
			if !present || !pythonJSONEqual(field.Value, value) {
				return false
			}
		}
		return true
	}
	return false
}

// pythonSubscriptError is value['key'] on a value that is not a dict.
func pythonSubscriptError(value any) error {
	switch value.(type) {
	case []any:
		return pythonFailure("list indices must be integers or slices, not str")
	case string:
		return pythonFailure("string indices must be integers, not 'str'")
	}
	return pythonFailure(fmt.Sprintf("'%s' object is not subscriptable", pythonTypeLabel(value)))
}

// pythonAttributeError is value.<name> on a value that has no such attribute.
func pythonAttributeError(value any, name string) error {
	return pythonFailure(fmt.Sprintf("'%s' object has no attribute '%s'", pythonTypeLabel(value), name))
}

// pythonIntError is int(str(value).strip()) raising ValueError.
func pythonIntError(value any) error {
	var text string
	switch v := value.(type) {
	case string:
		text = dto.PythonStrip(v)
	case json.Number, bool:
		text = dto.PythonStrip(dto.PythonStr(v))
	default:
		return domain.ErrSubscriptionCreateServer
	}
	return pythonFailure("invalid literal for int() with base 10: " + dto.PythonStringRepr(text))
}

// djangoInvalidUUID is str() of UUIDField.to_python's ValidationError.
func djangoInvalidUUID(text string) error {
	return pythonFailure("[" + dto.PythonStringRepr("“"+text+"” is not a valid UUID.") + "]")
}

// subscriptionAssociationGet is SportAssociation.objects.get(user=...) failing.
func subscriptionAssociationGet(count int) error {
	if count == 0 {
		return pythonFailure("SportAssociation matching query does not exist.")
	}
	if count > 20 {
		return pythonFailure("get() returned more than one SportAssociation -- it returned more than 20!")
	}
	return pythonFailure(fmt.Sprintf("get() returned more than one SportAssociation -- it returned %d!", count))
}

// namedAssociation is create_subscription's data['sport_association']: the
// association of the user with that username (str() of the value); a miss
// keeps request.user's. An association account only names its own
// association: another one is DRF's 403.
func (s *SubscriptionCreates) namedAssociation(ctx context.Context, tx domain.SubscriptionCreateTransaction, value any, own string) error {
	if value == nil {
		return nil // username=None matches no user
	}
	associations, err := tx.NamedAssociations(ctx, dto.PythonStr(value))
	if err != nil {
		return err
	}
	if len(associations) > 1 {
		// User.username is unique and SportAssociation.user is a OneToOneField (user_models.py:641).
		return domain.ErrSubscriptionCreateUnreachable
	}
	if len(associations) == 1 && associations[0] != own {
		return &domain.PermissionDenied{Detail: "User not allowed."}
	}
	return nil
}

// subscriptionNamedMember is User.objects.get(username=...) for
// data['sport_association'] (str() of the value, as the CharField lookup
// compares it; no live user: the 500 page).
func subscriptionNamedMember(ctx context.Context, tx domain.SubscriptionCreateTransaction, value any) (*domain.SubscriptionCreateMember, error) {
	if value == nil {
		// username=None is `username IS NULL`: no account.
		return nil, domain.ErrSubscriptionCreateServer
	}
	member, err := tx.MemberByUsername(ctx, dto.PythonStr(value))
	if err != nil {
		return nil, err
	}
	if member == nil {
		return nil, domain.ErrSubscriptionCreateServer
	}
	return member, nil
}

// namedUser is _prepare_subscription_request for an anonymous request:
// request.user becomes the user data['sport_association'] names; without the
// key the view reads AnonymousUser.role (the 500 page).
func (s *SubscriptionCreates) namedUser(ctx context.Context, data domain.RequestObject) (*domain.Account, error) {
	named, present := data.Lookup("sport_association")
	if !present {
		return nil, domain.ErrSubscriptionCreateServer
	}
	tx, err := s.store.BeginSubscriptionCreate(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback()
	member, err := subscriptionNamedMember(ctx, tx, named)
	if err != nil {
		return nil, err
	}
	return &domain.Account{ID: member.ID, Role: member.Role}, nil
}

// associateUpdateIndex is the audit index of an Associate update entry: the
// resolver reads the saved row through the default manager (live, group-aware
// rows, so an update that moves the row indexes through its new association),
// then changes_json, then the actor.
func (s *SubscriptionCreates) associateUpdateIndex(ctx context.Context, tx domain.SubscriptionCreateTransaction, audit subscriptionAudit,
	updated domain.SubscriptionEditAssociate, changes map[string][2]string) (*string, string, error) {
	if !updated.Deleted && updated.AssociationID != nil && audit.visible(&updated) {
		association := *updated.AssociationID
		return &association, "direct", nil
	}
	if pair, ok := changes["sport_association"]; ok {
		for _, candidate := range pair {
			if candidate == "" || candidate == "None" {
				continue
			}
			exists, err := tx.ActiveAssociationExists(ctx, candidate)
			if err != nil {
				return nil, "", err
			}
			if exists {
				value := candidate
				return &value, "changes_json.sport_association", nil
			}
		}
	}
	actor, err := s.actorAssociation(ctx, tx, audit.context)
	return actor, "actor.sport_association", err
}

// saveAssociateByID is AssociateSerializer.create with an associate_id: the
// row with that key (all_objects()) is saved with the validated data
// and deleted=False, unless the association has a row (any state) with the
// same tax code (iexact, the first by key), which is saved instead (the
// serializer compares instance.sport_association_id, a UUID, with the
// association instance, so that test never skips the lookup); an
// unknown key is created with it. A create the unique tax code index refuses
// keeps nothing (its savepoint is rolled back) and the serializer then updates
// the key's row or, failing that, the association's first row with the tax
// code (three attempts); a save that breaks the index is the IntegrityError
// create_subscription lets out.
func (s *SubscriptionCreates) saveAssociateByID(ctx context.Context, tx domain.SubscriptionCreateTransaction, audit subscriptionAudit,
	write dto.AssociateTutorWrite, association string, group *string) (*domain.SubscriptionEditAssociate, error) {
	text := *write.AssociateID
	parsed, err := paymentInfoParseUUID(text)
	if err != nil {
		return nil, djangoInvalidUUID(text)
	}
	id := parsed.String()
	instance, err := tx.AssociateAny(ctx, id)
	if err != nil {
		return nil, err
	}
	tax := write.Text["tax_code"]
	if instance == nil {
		row := domain.SubscriptionEditAssociate{ID: id, Sex: "A", CreationDate: s.now().UTC(), GroupID: group, Spelling: associateSpelling(id, text)}
		applyAssociateTutorWrite(&row, write)
		// max_retries = 3: each attempt is Associate.objects.create inside its own
		// atomic block; the except block takes the fallbacks and re-raises on the last.
		for attempt := 0; ; attempt++ {
			err = tx.InsertAssociateAttempt(ctx, row)
			if err == nil {
				if row.GroupID != nil {
					audit.inserted[row.AuditKey()] = true
				}
				own := association
				if err = audit.associate(ctx, row.AuditKey(), 0, true, &row, nil, &own, "direct"); err != nil {
					return nil, err
				}
				return &row, nil
			}
			var integrity *domain.AssociateIntegrityError
			if !errors.As(err, &integrity) {
				return nil, err
			}
			// except IntegrityError: the row another request made (the key), then the
			// association's first row with that tax code (any deleted state, the tax
			// code not upper-cased here).
			instance, err = tx.AssociateAny(ctx, id)
			if err != nil {
				return nil, err
			}
			if instance != nil {
				return s.saveAssociateRow(ctx, tx, audit, write, association, *instance, true)
			}
			if tax != nil && *tax != "" {
				other, err := tx.AssociateByTaxCode(ctx, *tax, association)
				if err != nil {
					return nil, err
				}
				if other != nil {
					return s.saveAssociateRow(ctx, tx, audit, write, association, *other, false)
				}
			}
			if attempt == 2 {
				return nil, &domain.SubscriptionCreateFailure{Text: integrity.Text}
			}
		}
	}
	target, keepsID := *instance, false
	if tax != nil && *tax != "" {
		other, err := tx.AssociateByTaxCode(ctx, *tax, association)
		if err != nil {
			return nil, err
		}
		if other != nil {
			// The target association's row is saved instead and keeps its key.
			target, keepsID = *other, true
		}
	}
	return s.saveAssociateRow(ctx, tx, audit, write, association, target, !keepsID)
}

// saveAssociateRow is the serializer's setattr loop and save(): every validated
// field (associate_id included, unless the row is the target association's) and
// deleted=False. A save the unique tax code index refuses is the IntegrityError
// the whole registration rolls back on.
func (s *SubscriptionCreates) saveAssociateRow(ctx context.Context, tx domain.SubscriptionCreateTransaction, audit subscriptionAudit,
	write dto.AssociateTutorWrite, association string, target domain.SubscriptionEditAssociate,
	takesID bool) (*domain.SubscriptionEditAssociate, error) {
	old := target
	row := target
	applyAssociateTutorWrite(&row, write)
	if takesID {
		row.Spelling = associateSpelling(row.ID, *write.AssociateID)
	}
	row.Deleted = false
	if err := tx.SaveAssociate(ctx, row); err != nil {
		return nil, associateSaveError(err)
	}
	if old.Deleted {
		// deleted=False is the statement that restores the row, and the partial
		// index checks the code at that point (a live row may hold it).
		if err := tx.SetAssociateDeleted(ctx, row.ID, false); err != nil {
			return nil, associateSaveError(err)
		}
		// The default manager hides the previous state: every field against None.
		created := map[string][2]string{"sport_association": {"None", subscriptionEditValue(row.AssociationID)}}
		// Indexed from the stored row, which the default manager still hides.
		index, path, err := s.associateUpdateIndex(ctx, tx, audit, old, created)
		if err != nil {
			return nil, err
		}
		if err = audit.associate(ctx, row.AuditKey(), 1, true, &row, nil, index, path); err != nil {
			return nil, err
		}
		return &row, nil
	}
	changes := associateChanges(old, row)
	// auditlog writes the entry in pre_save: its index reads the stored row,
	// still in the association the update starts from.
	index, path, err := s.associateUpdateIndex(ctx, tx, audit, old, changes)
	if err != nil {
		return nil, err
	}
	if err = audit.associate(ctx, row.AuditKey(), 1, false, &old, changes, index, path); err != nil {
		return nil, err
	}
	return &row, nil
}

// associateSpelling is the key the Django instance keeps: the text a client
// sent when it names another spelling of the stored (canonical) uuid.
func associateSpelling(id, text string) string {
	if text == id {
		return ""
	}
	return text
}

// associateSaveError is the IntegrityError of save() as create_subscription
// raises it: renew answers {"status": "error", "msg": str(e)}, add the 500 page.
func associateSaveError(err error) error {
	var integrity *domain.AssociateIntegrityError
	if errors.As(err, &integrity) {
		return &domain.SubscriptionCreateFailure{Text: integrity.Text}
	}
	return err
}
