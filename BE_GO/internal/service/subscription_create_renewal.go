package service

import (
	"context"
	"encoding/json"
	"math/big"
	"strconv"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
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
			// user__username=<value>: the CharField compares str(value).
			username = dto.PythonRepr(named)
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
	key, found := renewalSourceKey(value)
	if !found {
		return nil, nil // ValidationError: no subscription
	}
	source, err := tx.RenewalSource(ctx, key, association)
	if err != nil || source == nil {
		return nil, err
	}
	if source.OwnerID == requester || (source.UserID != nil && *source.UserID == requester) {
		return source, nil
	}
	return nil, nil
}

// renewalSourceKey is UUIDField.to_python(data['subscription_id']): a string
// through uuid.UUID(hex), an int (bool included) through uuid.UUID(int=...)
// when it is in 0..2**128-1; anything else is a ValidationError.
func renewalSourceKey(value any) (string, bool) {
	var number *big.Int
	switch v := value.(type) {
	case string:
		parsed, err := paymentInfoParseUUID(v)
		if err != nil {
			return "", false
		}
		return parsed.String(), true
	case bool:
		number = big.NewInt(0)
		if v {
			number = big.NewInt(1)
		}
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return "", false // a float
		}
		parsed, ok := new(big.Int).SetString(string(v), 10)
		if !ok {
			return "", false
		}
		number = parsed
	default:
		return "", false
	}
	if number.Sign() < 0 || number.BitLen() > 128 {
		return "", false
	}
	var raw [16]byte
	number.FillBytes(raw[:])
	return uuid.UUID(raw).String(), true
}

// renewalPerson is AssociateSerializer(associate).data in Meta.fields order,
// born_date as DD/MM/YYYY (None stays None).
func renewalPerson(associate *domain.SubscriptionEditAssociate, owner string) domain.RequestObject {
	text := func(value *string) any {
		if value == nil {
			return nil
		}
		return *value
	}
	account := any(nil)
	if associate.UserID != nil {
		account = *associate.UserID
	} else if associate.AssociationID != nil {
		account = owner
	}
	var born any
	if associate.BornDate != nil {
		born = associate.BornDate.Format("02/01/2006")
	}
	return domain.RequestObject{
		{Name: "associate_id", Value: associate.ID}, {Name: "address", Value: text(associate.Address)},
		{Name: "address_cap", Value: text(associate.AddressCap)}, {Name: "address_city", Value: text(associate.AddressCity)},
		{Name: "born_city", Value: text(associate.BornCity)}, {Name: "born_date", Value: born},
		{Name: "email", Value: text(associate.Email)}, {Name: "sex", Value: associate.Sex},
		{Name: "first_name", Value: text(associate.FirstName)}, {Name: "last_name", Value: text(associate.LastName)},
		{Name: "is_minor", Value: associate.IsMinor}, {Name: "sport_association", Value: text(associate.AssociationID)},
		{Name: "tax_code", Value: text(associate.TaxCode)}, {Name: "user", Value: account}, {Name: "phone", Value: text(associate.Phone)},
		{Name: "family", Value: text(associate.FamilyID)}, {Name: "family_role", Value: text(associate.FamilyRole)},
	}
}

// mainTutor is Associate.get_main_tutor: the primary relation's tutor; ""
// without relations.
func (s *SubscriptionCreates) mainTutor(ctx context.Context, tx domain.SubscriptionCreateTransaction, associate string) (string, error) {
	relations, err := tx.Relations(ctx, associate)
	if err != nil || len(relations) == 0 {
		return "", err
	}
	for _, relation := range relations {
		if relation.IsPrimary {
			return relation.TutorID, nil
		}
	}
	// Relations without a primary one (get_main_tutor makes the first primary) are not ported.
	return "", domain.ErrSubscriptionCreateNotPorted
}

// groupedCustomData is _get_asd_custom_data_for_grouped_subscriptions: the
// club data of the account's latest subscription whose custom data text
// contains 'asd-o-ssd' or 'scuola-asc'; nothing without one.
func groupedCustomData(ctx context.Context, tx domain.SubscriptionCreateTransaction, user string) (domain.RequestObject, error) {
	rows, err := tx.GroupedCustomData(ctx, user)
	if err != nil || len(rows) == 0 {
		return nil, err
	}
	row := rows[0]
	if len(rows) > 1 && rows[0].CreationDate.Equal(rows[1].CreationDate) {
		// order_by('-creation_date').first(): the database breaks the tie, as in Django.
		newest, err := tx.NewestGroupedCustomData(ctx, user)
		if err != nil {
			return nil, err
		}
		if newest == nil {
			return nil, domain.ErrSubscriptionCreateServer
		}
		row = *newest
	}
	parsed, err := dto.ParsePythonJSON(row.CustomData)
	if err != nil {
		return nil, err
	}
	stored, isObject := parsed.(domain.RequestObject)
	if !isObject {
		return nil, pythonAttributeError(parsed, "get")
	}
	get := func(key string, fallback any) any {
		if value, present := stored.Lookup(key); present {
			return value
		}
		return fallback
	}
	return domain.RequestObject{
		{Name: "denomination", Value: get("denomination", "")}, {Name: "city", Value: get("city", "")},
		{Name: "province", Value: get("province", "")}, {Name: "address", Value: get("address", "")},
		{Name: "cap", Value: get("zip", "")}, {Name: "country", Value: get("country", "Italia")},
		{Name: "tax_code", Value: get("tax_code", "")}, {Name: "email", Value: get("email", "")},
	}, nil
}

// fillPreregistration is fill_data_preregisration: the request's person,
// certificate, custom data, role and number are replaced by those of the
// renewed subscription; the registration keeps the subscription type sent
// with the request; the member's main tutor is sent as associate_tutor_data.
// It reads only; user is the renewed subscription's account.
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
	tutorID, err := s.mainTutor(ctx, tx, associate.ID)
	if err != nil {
		return nil, nil, err
	}
	var tutorData domain.RequestObject
	if tutorID != "" {
		tutor, err := tx.AssociateAny(ctx, tutorID)
		if err != nil {
			return nil, nil, err
		}
		if tutor == nil {
			return nil, nil, domain.ErrSubscriptionCreateServer
		}
		if tutor.UserID == nil && tutor.AssociationID != nil && (associate.AssociationID == nil || *tutor.AssociationID != *associate.AssociationID) {
			// A tutor without an account stored in another association is not ported.
			return nil, nil, domain.ErrSubscriptionCreateNotPorted
		}
		tutorData = renewalPerson(tutor, source.OwnerID)
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
	// AssociateSerializer(subscription.associate).data, then the keys the view sets.
	filled := append(renewalPerson(associate, source.OwnerID), domain.RequestField{Name: "type", Value: kind},
		domain.RequestField{Name: "role", Value: json.Number(strconv.FormatInt(source.Role, 10))},
		domain.RequestField{Name: "subscription_number", Value: text(source.Number)})
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
	if tutorData != nil {
		data = objectSet(data, "associate_tutor_data", tutorData)
	}
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
