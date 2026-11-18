package service

import (
	"sort"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
)

// consumerImportTaxConflicts is AssociationImportService._legacy_tax_code_conflicts:
// active members of the source association keep their tax codes; the other
// rows, in (has association, UUID) order, keep a code only while it is free.
// It returns {source associate id: associate id keeping the code}. Values the
// reproduced to_python subset cannot convert are handed to Django.
func consumerImportTaxConflicts(records []any, association string) (map[string]string, error) {
	type row struct {
		id, tax        string
		hasAssociation bool
	}
	active := map[string]string{}
	others := []row{}
	for _, raw := range records {
		record, ok := raw.(domain.RequestObject)
		if !ok {
			continue
		}
		id, err := importStr(importGetValue(record, "associate_id"))
		if err != nil {
			return nil, errConsumerImportUnsupported
		}
		deleted := false
		if value, present := importGet(record, "deleted"); present {
			if deleted, err = restoreBool(value); err != nil {
				return nil, errConsumerImportUnsupported
			}
		}
		tax := ""
		value, present := importGet(record, "tax_code")
		hasTax := present && value != nil
		if hasTax {
			if _, scalar := value.(string); !scalar {
				return nil, errConsumerImportUnsupported
			}
			tax = restoreUpper(dto.PythonStr(value))
		}
		source := importGetValue(record, "sport_association_id")
		text, _ := restoreStr(source)
		if !importTruthy(source) || text != association {
			if !deleted && hasTax {
				others = append(others, row{id: id, tax: tax, hasAssociation: importTruthy(source)})
			}
			continue
		}
		if deleted || !hasTax {
			continue
		}
		if _, duplicate := active[tax]; duplicate {
			return nil, importError("Codice fiscale duplicato tra le anagrafiche attive: " + id + ".")
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
		if keeper, taken := active[item.tax]; taken {
			conflicts[item.id] = keeper
			continue
		}
		active[item.tax] = item.id
	}
	return conflicts, nil
}
