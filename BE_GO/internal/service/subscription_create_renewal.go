package service

import (
	"context"
	"encoding/json"
	"strconv"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

var errSubscriptionRenewalSource = &domain.SubscriptionCreateInvalid{
	Detail: map[string]string{"msg": "Iscrizione da rinnovare non trovata.", "code": "400"}}

// subscriptionRenewalRequested is create_subscription's test for a
// preregistration that renews a subscription: data['preregistration'] is True
// and data['subscription_id'] is not None.
func subscriptionRenewalRequested(data domain.RequestObject) bool {
	if flag, _ := data.Lookup("preregistration"); flag != true {
		return false
	}
	source, _ := data.Lookup("subscription_id")
	return source != nil
}

// renewalSource is _renewal_source: the subscription data['subscription_id']
// names in the association of the registration (the one
// data['sport_association'] names, else user.sport_association), when the
// requester is that association's account or the subscription's own account.
// nil: fill_data_preregisration's 400.
func (s *SubscriptionCreates) renewalSource(ctx context.Context, tx domain.SubscriptionCreateTransaction, identity domain.ResolvedIdentity,
	data domain.RequestObject, request SubscriptionCreateRequest) (*domain.SubscriptionRenewalSource, error) {
	if request.Anonymous {
		return nil, nil
	}
	user := identity.ScopeUser
	requester := user.ID
	if request.RequesterID != "" {
		requester = request.RequesterID
	}
	association := ""
	if named, _ := data.Lookup("sport_association"); subscriptionTruthy(named) {
		username, isText := named.(string)
		if !isText {
			// Numbers, booleans and containers as a username are not ported.
			return nil, domain.ErrSubscriptionCreateNotPorted
		}
		associations, err := tx.NamedAssociations(ctx, username)
		if err != nil {
			return nil, err
		}
		if len(associations) > 1 {
			return nil, domain.ErrSubscriptionCreateNotPorted
		}
		if len(associations) == 0 {
			return nil, nil
		}
		association = associations[0]
	} else {
		// user.sport_association.
		if user.Role == domain.RoleAthlete {
			return nil, &domain.PermissionDenied{Detail: "User is not a sport association"}
		}
		settings, count, err := tx.Settings(ctx, user.ID)
		if err != nil {
			return nil, err
		}
		if count != 1 {
			return nil, subscriptionAssociationGet(count)
		}
		association = settings.AssociationID
	}
	value, _ := data.Lookup("subscription_id")
	text, isText := value.(string)
	if !isText {
		// Integer keys (uuid.UUID(int=...)) and other values are not ported.
		return nil, domain.ErrSubscriptionCreateNotPorted
	}
	parsed, err := paymentInfoParseUUID(text)
	if err != nil {
		return nil, nil // ValidationError: no subscription
	}
	source, err := tx.RenewalSource(ctx, parsed.String(), association)
	if err != nil || source == nil {
		return nil, err
	}
	if source.OwnerID == requester || (source.UserID != nil && *source.UserID == requester) {
		return source, nil
	}
	return nil, nil
}

// fillPreregistration is fill_data_preregisration: the request's person,
// certificate, custom data, role and number are replaced by those of the
// renewed subscription; the registration keeps the subscription type sent
// with the request. It reads only; user is the renewed subscription's account.
func (s *SubscriptionCreates) fillPreregistration(ctx context.Context, tx domain.SubscriptionCreateTransaction, identity domain.ResolvedIdentity,
	data domain.RequestObject, request SubscriptionCreateRequest) (domain.RequestObject, *string, error) {
	source, err := s.renewalSource(ctx, tx, identity, data, request)
	if err != nil {
		return nil, nil, err
	}
	if source == nil {
		return nil, nil, errSubscriptionRenewalSource
	}
	// sub_type = data['associate_data']['type'].
	sent, present := data.Lookup("associate_data")
	if !present {
		return nil, nil, pythonKeyError("associate_data")
	}
	object, isObject := sent.(domain.RequestObject)
	if !isObject {
		return nil, nil, pythonSubscriptError(sent)
	}
	kind, present := object.Lookup("type")
	if !present {
		return nil, nil, pythonKeyError("type")
	}
	if source.AssociateID == nil {
		// AssociateSerializer(None).data.
		return nil, nil, domain.ErrSubscriptionCreateNotPorted
	}
	associate, err := tx.AssociateAny(ctx, *source.AssociateID)
	if err != nil {
		return nil, nil, err
	}
	if associate == nil {
		return nil, nil, domain.ErrSubscriptionCreateServer
	}
	relations, err := tx.Relations(ctx, associate.ID)
	if err != nil {
		return nil, nil, err
	}
	if len(relations) > 0 {
		// The renewed member's tutor (get_main_tutor, its serialized data) is not ported.
		return nil, nil, domain.ErrSubscriptionCreateNotPorted
	}
	if associate.BornDate == nil {
		// subscription.associate.born_date.strftime on None.
		return nil, nil, pythonAttributeError(nil, "strftime")
	}
	text := func(value *string) any {
		if value == nil {
			return nil
		}
		return *value
	}
	owner := any(nil)
	if associate.UserID != nil {
		owner = *associate.UserID
	} else if associate.AssociationID != nil {
		owner = source.OwnerID
	}
	// AssociateSerializer(subscription.associate).data in Meta.fields order, then the keys the view sets.
	filled := domain.RequestObject{
		{Name: "associate_id", Value: associate.ID}, {Name: "address", Value: text(associate.Address)},
		{Name: "address_cap", Value: text(associate.AddressCap)}, {Name: "address_city", Value: text(associate.AddressCity)},
		{Name: "born_city", Value: text(associate.BornCity)}, {Name: "born_date", Value: associate.BornDate.Format("02/01/2006")},
		{Name: "email", Value: text(associate.Email)}, {Name: "sex", Value: associate.Sex},
		{Name: "first_name", Value: text(associate.FirstName)}, {Name: "last_name", Value: text(associate.LastName)},
		{Name: "is_minor", Value: associate.IsMinor}, {Name: "sport_association", Value: text(associate.AssociationID)},
		{Name: "tax_code", Value: text(associate.TaxCode)}, {Name: "user", Value: owner}, {Name: "phone", Value: text(associate.Phone)},
		{Name: "family", Value: text(associate.FamilyID)}, {Name: "family_role", Value: text(associate.FamilyRole)},
		{Name: "type", Value: kind}, {Name: "role", Value: json.Number(strconv.FormatInt(source.Role, 10))},
		{Name: "subscription_number", Value: text(source.Number)},
	}
	medical := domain.RequestObject{{Name: "medical_id", Value: nil}, {Name: "certificate_expring_date", Value: nil}}
	if source.MedicalID != nil {
		medical[0].Value = *source.MedicalID
		expiration, err := tx.CertificateExpiration(ctx, *source.MedicalID)
		if err != nil {
			return nil, nil, err
		}
		if expiration != nil {
			medical[1].Value = expiration.Format("02/01/2006")
		}
	}
	stored := func(raw *string) (any, error) {
		if raw == nil {
			return nil, nil
		}
		return dto.ParsePythonJSON(*raw)
	}
	additional, err := stored(source.AdditionalFields)
	if err != nil {
		return nil, nil, err
	}
	custom, err := stored(source.CustomData)
	if err != nil {
		return nil, nil, err
	}
	// The request object is not shared with the caller's copy.
	data = append(domain.RequestObject{}, data...)
	data = objectSet(data, "associate_data", filled)
	data = objectSet(data, "medical_certificate", medical)
	data = objectSet(data, "additional_fields", additional)
	data = objectSet(data, "custom_data", custom)
	data = objectSet(data, "subscription_membership", domain.RequestObject{
		{Name: "membership_number", Value: text(source.Number)}, {Name: "membership_type", Value: text(source.SubscriptionType)}})
	data = objectSet(data, "signature", domain.RequestObject{
		{Name: "data", Value: nil}, {Name: "signature", Value: nil}, {Name: "there_is_signature", Value: false}})
	data = objectSet(data, "new_user_account", domain.RequestObject{{Name: "new_member", Value: false}})
	return data, source.UserID, nil
}
