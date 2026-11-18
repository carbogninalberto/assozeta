package service

import (
	"sort"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/importeffects"
)

// consumerImportTaxConflicts is AssociationImportService._legacy_tax_code_conflicts:
// active members of the source association keep their tax codes; the other
// rows, in (has association, UUID) order, keep a code only while it is free.
// It returns {source associate id: associate id keeping the code}.
func consumerImportTaxConflicts(model *importeffects.Model, records []any, association string) (map[string]string, error) {
	deletedField := model.Field("deleted")
	// deleted_field.to_python(record.get('deleted', False)) and
	// tax_field.to_python(record['tax_code']).upper() for an active row.
	activeTax := func(record domain.RequestObject) (string, bool, error) {
		raw, present := importGet(record, "deleted")
		if !present {
			raw = false
		}
		deleted, err := importeffects.BoolToPython(deletedField, raw)
		if err != nil {
			return "", false, err
		}
		value, _ := importGet(record, "tax_code")
		if importeffects.Truthy(deleted) || value == nil {
			return "", false, nil
		}
		text, err := restoreStr(value)
		if err != nil {
			return "", false, err
		}
		return restoreUpper(text), true, nil
	}
	type row struct {
		record         domain.RequestObject
		id             string
		hasAssociation bool
	}
	active := map[string]string{}
	others := []row{}
	for _, raw := range records {
		record, ok := raw.(domain.RequestObject)
		if !ok {
			continue
		}
		source := importGetValue(record, "sport_association_id")
		text, err := restoreStr(source)
		if err != nil {
			return nil, err
		}
		id, err := restoreStr(importGetValue(record, "associate_id"))
		if err != nil {
			return nil, err
		}
		if !importTruthy(source) || text != association {
			others = append(others, row{record: record, id: id, hasAssociation: importTruthy(source)})
			continue
		}
		tax, has, err := activeTax(record)
		if err != nil {
			return nil, err
		}
		if !has {
			continue
		}
		if _, duplicate := active[tax]; duplicate {
			return nil, &importeffects.DjangoError{Class: "ValueError",
				Message: "Codice fiscale duplicato tra le anagrafiche attive: " + id + "."}
		}
		active[tax] = id
	}
	sort.SliceStable(others, func(i, j int) bool {
		if others[i].hasAssociation != others[j].hasAssociation {
			return !others[i].hasAssociation
		}
		return restorePyLess(others[i].id, others[j].id)
	})
	conflicts := map[string]string{}
	for _, item := range others {
		tax, has, err := activeTax(item.record)
		if err != nil {
			return nil, err
		}
		if !has {
			continue
		}
		if keeper, taken := active[tax]; taken {
			conflicts[item.id] = keeper
			continue
		}
		active[tax] = item.id
	}
	return conflicts, nil
}
