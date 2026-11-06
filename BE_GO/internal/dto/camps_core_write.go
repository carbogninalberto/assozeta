package dto

import "github.com/carbogninalberto/assozeta/BE_GO/internal/domain"

// CampInput is CampsAndRetreatsSerializer's field-level validation; Errors
// holds codes by field, the service adds the lookups.
type CampInput struct {
	ID          CampsRelation
	Title       CampsValue[*string]
	Description CampsValue[*string]
	Association CampsRelation
	Errors      map[string][]string
}

// CampFieldOrder is the serializer's writable fields: the pk, model fields,
// then the forward relation.
var CampFieldOrder = []string{"camps_and_retreats_id", "title", "description", "sport_association"}

// CampAssignAssociation is camps_and_retreats_add's
// request.data['sport_association'] = association before validation: a form
// QueryDict is immutable and only a mapping supports item assignment.
func CampAssignAssociation(body any, form bool, association string) (domain.RequestObject, error) {
	if form {
		if object, ok := body.(domain.RequestObject); ok {
			for _, field := range object {
				// With files DRF merges them into a mutable copy (not ported).
				if _, upload := field.Value.(domain.RequestUpload); upload {
					return nil, ErrCampsFormNotPorted
				}
			}
		}
		return nil, &CampsPythonError{Message: "This QueryDict instance is immutable"}
	}
	switch data := body.(type) {
	case domain.RequestObject:
		copied := append(domain.RequestObject(nil), data...)
		for index := range copied {
			if copied[index].Name == "sport_association" {
				copied[index].Value = association
				return copied, nil
			}
		}
		return append(copied, domain.RequestField{Name: "sport_association", Value: association}), nil
	case []any:
		return nil, &CampsPythonError{Message: "list indices must be integers or slices, not str"}
	}
	return nil, &CampsPythonError{Message: "'" + pythonTypeName(body) + "' object does not support item assignment"}
}

// ParseCampWrite ports CampsAndRetreatsSerializer(data=..., partial=...) up
// to the queryset lookups. A nil input with nil error is a non-mapping body
// whose errors are in the returned map.
func ParseCampWrite(body any, form, partial bool) (*CampInput, map[string][]string, error) {
	if body == nil {
		return nil, campsNonDict(nil), nil
	}
	object, ok := body.(domain.RequestObject)
	if !ok {
		return nil, campsNonDict(body), nil
	}
	reader, err := campsInput(object, form)
	if err != nil {
		return nil, nil, err
	}
	reader.partial = partial
	input := &CampInput{}
	input.ID = reader.uuid("camps_and_retreats_id")
	input.Title = reader.text("title", 255)
	input.Description = reader.text("description", 2048)
	// sport_association has the model default None: not required, not null.
	input.Association = reader.relation("sport_association", false, false)
	input.Errors = reader.errors
	return input, nil, nil
}
