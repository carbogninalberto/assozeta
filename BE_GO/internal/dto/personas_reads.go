package dto

import (
	"encoding/json"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// PersonaOptional renders a nullable text column.
func PersonaOptional(value *string) any {
	if value == nil {
		return nil
	}
	return *value
}

func personaDate(value *time.Time) any {
	if value == nil {
		return nil
	}
	return value.Format("2006-01-02")
}

// PersonaAge is Associate.calculate_age(): 0 without a born date, else
// int((today - born).days / 365.25).
func PersonaAge(born *time.Time, today time.Time) int64 {
	if born == nil {
		return 0
	}
	// Day counts from Unix seconds: time.Duration saturates beyond ~292 years.
	days := (today.Unix() - time.Date(born.Year(), born.Month(), born.Day(), 0, 0, 0, 0, time.UTC).Unix()) / 86400
	return int64(float64(days) / 365.25)
}

// personaFullName is '%s %s' % (first_name, last_name), stripped (None renders as 'None').
func personaFullName(row domain.PersonaAssociate) string {
	text := func(value *string) string {
		if value == nil {
			return "None"
		}
		return *value
	}
	return pythonStrip(text(row.FirstName) + " " + text(row.LastName))
}

// PersonaSearchItem is AssociateSearchSerializer: tutors are the prefetched relations.
func PersonaSearchItem(row domain.PersonaListed, relations []domain.PersonaRelation, today time.Time) domain.RequestObject {
	tutors := []any{}
	for _, relation := range relations {
		if relation.AssociateID != row.ID {
			continue
		}
		tutors = append(tutors, domain.RequestObject{{Name: "tutor", Value: domain.RequestObject{
			{Name: "first_name", Value: PersonaOptional(relation.FirstName)}, {Name: "last_name", Value: PersonaOptional(relation.LastName)},
			{Name: "associate_id", Value: relation.ID}}}})
	}
	return domain.RequestObject{{Name: "associate_id", Value: row.ID}, {Name: "first_name", Value: PersonaOptional(row.FirstName)},
		{Name: "last_name", Value: PersonaOptional(row.LastName)}, {Name: "born_date", Value: personaDate(row.BornDate)},
		{Name: "is_tutor", Value: row.IsTutor}, {Name: "age", Value: personaInt(PersonaAge(row.BornDate, today))},
		{Name: "notes", Value: PersonaOptional(row.Notes)}, {Name: "tutors", Value: tutors},
		{Name: "picture_path", Value: PersonaOptional(row.PicturePath)}}
}

func personaInt(value int64) json.Number { return json.Number(strconv.FormatInt(value, 10)) }

// personaModelFields are the '__all__' non-relation fields in model order.
func personaModelFields(row domain.PersonaAssociate) domain.RequestObject {
	text := PersonaOptional
	return domain.RequestObject{{Name: "picture_path", Value: text(row.PicturePath)}, {Name: "first_name", Value: text(row.FirstName)},
		{Name: "last_name", Value: text(row.LastName)}, {Name: "sex", Value: row.Sex}, {Name: "tax_code", Value: text(row.TaxCode)},
		{Name: "born_date", Value: personaDate(row.BornDate)}, {Name: "born_city", Value: text(row.BornCity)},
		{Name: "address_city", Value: text(row.AddressCity)}, {Name: "address", Value: text(row.Address)},
		{Name: "address_cap", Value: text(row.AddressCap)}, {Name: "nationality", Value: text(row.Nationality)},
		{Name: "nationality_residence", Value: text(row.NationalityResidence)}, {Name: "email", Value: text(row.Email)},
		{Name: "phone", Value: text(row.Phone)}, {Name: "phone_2", Value: text(row.Phone2)}, {Name: "phone_3", Value: text(row.Phone3)},
		{Name: "phone_4", Value: text(row.Phone4)}, {Name: "phone_label", Value: text(row.PhoneLabel)},
		{Name: "phone_2_label", Value: text(row.Phone2Label)}, {Name: "phone_3_label", Value: text(row.Phone3Label)},
		{Name: "phone_4_label", Value: text(row.Phone4Label)}, {Name: "is_minor", Value: row.IsMinor}, {Name: "disabled", Value: row.Disabled},
		{Name: "draft", Value: row.Draft}, {Name: "deleted", Value: row.Deleted}, {Name: "family_role", Value: text(row.FamilyRole)},
		{Name: "id_number", Value: text(row.IDNumber)}, {Name: "creation_date", Value: DRFUTCDateTime(row.CreationDate)},
		{Name: "migrated", Value: row.Migrated}, {Name: "notes", Value: text(row.Notes)}}
}

// personaRelationFields are the forward relations as primary keys.
func personaRelationFields(row domain.PersonaAssociate) domain.RequestObject {
	return domain.RequestObject{{Name: "user", Value: PersonaOptional(row.UserID)}, {Name: "sport_association", Value: PersonaOptional(row.AssociationID)},
		{Name: "group", Value: PersonaOptional(row.GroupID)}, {Name: "family", Value: PersonaOptional(row.FamilyID)}}
}

func personaTutorsOf(id string, relations []domain.PersonaRelation) []domain.PersonaAssociate {
	tutors := []domain.PersonaAssociate{}
	for _, relation := range relations {
		if relation.AssociateID == id {
			tutors = append(tutors, relation.PersonaAssociate)
		}
	}
	return tutors
}

// personaBasicTutor is BasicTutorsSerializer: '__all__' plus full_name; its
// tutors are the M2M manager's primary keys.
func personaBasicTutor(row domain.PersonaAssociate, m2m []domain.PersonaRelation) domain.RequestObject {
	pks := []any{}
	for _, tutor := range personaTutorsOf(row.ID, m2m) {
		pks = append(pks, tutor.ID)
	}
	item := domain.RequestObject{{Name: "associate_id", Value: row.ID}, {Name: "full_name", Value: personaFullName(row)}}
	item = append(append(item, personaModelFields(row)...), personaRelationFields(row)...)
	return append(item, domain.RequestField{Name: "tutors", Value: pks})
}

// personaBasicAssociate is BasicAssociateSerializer: declared tutors nest
// BasicTutorsSerializer over the M2M manager.
func personaBasicAssociate(row domain.PersonaAssociate, second, third []domain.PersonaRelation) domain.RequestObject {
	tutors := []any{}
	for _, tutor := range personaTutorsOf(row.ID, second) {
		tutors = append(tutors, personaBasicTutor(tutor, third))
	}
	item := domain.RequestObject{{Name: "associate_id", Value: row.ID}, {Name: "full_name", Value: personaFullName(row)},
		{Name: "tutors", Value: tutors}}
	return append(append(item, personaModelFields(row)...), personaRelationFields(row)...)
}

// PersonaInfo is AssociateSerializer (personas_serializers): declared fields,
// then the model fields and forward relations; write-only tutors_data and the
// attribute-less incomplete are omitted. relations are the prefetched tutor
// relations (base manager); second and third the M2M managers' tutors one and
// two levels down.
func PersonaInfo(row domain.PersonaListed, family []string, relations, second, third []domain.PersonaRelation, today time.Time) domain.RequestObject {
	age := PersonaAge(row.BornDate, today)
	members := make([]any, len(family))
	for i, member := range family {
		members[i] = member
	}
	tutors := []any{}
	for _, relation := range relations {
		tutors = append(tutors, domain.RequestObject{{Name: "tutor", Value: personaBasicAssociate(relation.PersonaAssociate, second, third)},
			{Name: "is_primary", Value: relation.IsPrimary}})
	}
	item := domain.RequestObject{{Name: "associate_id", Value: row.ID}, {Name: "full_name", Value: personaFullName(row.PersonaAssociate)},
		{Name: "is_minor_now", Value: age < 18}, {Name: "is_tutor", Value: row.IsTutor}, {Name: "age", Value: personaInt(age)},
		{Name: "family_members", Value: members}, {Name: "tutors", Value: tutors}}
	return append(append(item, personaModelFields(row.PersonaAssociate)...), personaRelationFields(row.PersonaAssociate)...)
}
