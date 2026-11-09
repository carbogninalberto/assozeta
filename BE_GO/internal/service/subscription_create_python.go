package service

import (
	"context"
	"encoding/json"
	"fmt"

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
// keeps request.user's. Another association is not ported.
func (s *SubscriptionCreates) namedAssociation(ctx context.Context, tx domain.SubscriptionCreateTransaction, value any, own string) error {
	if value == nil {
		return nil // username=None matches no user
	}
	associations, err := tx.NamedAssociations(ctx, dto.PythonStr(value))
	if err != nil {
		return err
	}
	if len(associations) > 1 || len(associations) == 1 && associations[0] != own {
		return domain.ErrSubscriptionCreateNotPorted
	}
	return nil
}

// associateUpdateIndex is the audit index of an Associate update entry, which
// auditlog writes before the UPDATE: the resolver reads the row through
// the default manager (live rows), then changes_json, then the actor.
func (s *SubscriptionCreates) associateUpdateIndex(ctx context.Context, tx domain.SubscriptionCreateTransaction, audit subscriptionAudit,
	old domain.SubscriptionEditAssociate, changes map[string][2]string) (*string, string, error) {
	if !old.Deleted && old.AssociationID != nil {
		association := *old.AssociationID
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
// same tax code (iexact, the first by key), which is saved instead; an
// unknown key is created with it.
func (s *SubscriptionCreates) saveAssociateByID(ctx context.Context, tx domain.SubscriptionCreateTransaction, audit subscriptionAudit,
	write dto.AssociateTutorWrite, association string, minorOf *string) (*domain.SubscriptionEditAssociate, error) {
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
	if tax != nil && !dto.AssociateTaxCodeASCII(*tax) {
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	if instance == nil {
		if id != text {
			// The key would be stored and audited as sent.
			return nil, domain.ErrSubscriptionCreateNotPorted
		}
		if tax != nil {
			// The IntegrityError fallbacks of the unique tax code index are not ported.
			if taken, err := tx.LiveTaxCodeTaken(ctx, *tax, association, id); err != nil || taken {
				return nil, notPortedOr(err)
			}
		}
		row := domain.SubscriptionEditAssociate{ID: id, Sex: "A", CreationDate: s.now().UTC()}
		applyAssociateTutorWrite(&row, write)
		if err = tx.InsertAssociate(ctx, row); err != nil {
			return nil, err
		}
		own := association
		if err = audit.associate(ctx, row.ID, 0, true, nil, &own, "direct"); err != nil {
			return nil, err
		}
		return &row, nil
	}
	target := instance
	if tax != nil && *tax != "" {
		other, err := tx.AssociateByTaxCode(ctx, *tax, association)
		if err != nil {
			return nil, err
		}
		if other != nil {
			target = other
		}
	}
	if minorOf != nil && target.ID == *minorOf {
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	old := *target
	row := *target
	applyAssociateTutorWrite(&row, write)
	row.Deleted = false
	if row.TaxCode != nil && row.AssociationID != nil {
		if !dto.AssociateTaxCodeASCII(*row.TaxCode) {
			return nil, domain.ErrSubscriptionCreateNotPorted
		}
		// A save that breaks the unique tax code index (IntegrityError) is not ported.
		if taken, err := tx.LiveTaxCodeTaken(ctx, *row.TaxCode, *row.AssociationID, row.ID); err != nil || taken {
			return nil, notPortedOr(err)
		}
	}
	if err = tx.SaveAssociate(ctx, row); err != nil {
		return nil, err
	}
	if old.Deleted {
		if err = tx.SetAssociateDeleted(ctx, row.ID, false); err != nil {
			return nil, err
		}
		// The default manager hides the previous state: every field against None.
		created := map[string][2]string{"sport_association": {"None", subscriptionEditValue(row.AssociationID)}}
		index, path, err := s.associateUpdateIndex(ctx, tx, audit, old, created)
		if err != nil {
			return nil, err
		}
		if err = audit.associate(ctx, row.ID, 1, true, nil, index, path); err != nil {
			return nil, err
		}
		return &row, nil
	}
	changes := associateChanges(old, row)
	index, path, err := s.associateUpdateIndex(ctx, tx, audit, old, changes)
	if err != nil {
		return nil, err
	}
	if err = audit.associate(ctx, row.ID, 1, false, changes, index, path); err != nil {
		return nil, err
	}
	return &row, nil
}

func notPortedOr(err error) error {
	if err != nil {
		return err
	}
	return domain.ErrSubscriptionCreateNotPorted
}
