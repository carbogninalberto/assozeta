package dto

import "github.com/carbogninalberto/assozeta/BE_GO/internal/domain"

// ArchiveUpdateDate is DateField's 'invalid' code (DATE_INPUT_FORMATS ['%d/%m/%Y']).
const ArchiveUpdateDate = "archive-date-format"

// ArchiveUpdateUnique is the primary key's UniqueValidator code.
const ArchiveUpdateUnique = "unique:sport association documents archive:sport association documents archive id"

// ArchiveUpdate is DocumentArchiveSerializer(instance, data=..., partial=True)
// field input before the related lookups; document is read-only.
type ArchiveUpdate struct {
	ID                     *string
	Association            *string
	AssociationRaw         any
	Date                   *string
	FolderPresent          bool
	Folder                 *FolderPK
	IDPresent, DatePresent bool
}

// ParseArchiveUpdate validates the present fields in serializer order;
// errors are codes per field (the related lookups follow in the service).
func ParseArchiveUpdate(object domain.RequestObject) (ArchiveUpdate, map[string][]string, error) {
	var update ArchiveUpdate
	invalid := map[string][]string{}
	for _, field := range object {
		if _, upload := field.Value.(domain.RequestUpload); upload {
			return update, nil, domain.ErrDocumentWritesNotPorted
		}
	}
	if value, present := object.Lookup("sport_association_documents_archive_id"); present {
		update.IDPresent = true
		// A boolean is an int for UUIDField: uuid.UUID(int=True) is ...0001.
		if id, codes := drfUUIDField(value); codes != nil {
			invalid["sport_association_documents_archive_id"] = codes
		} else {
			update.ID = &id
		}
	}
	if value, present := object.Lookup("sport_association"); present {
		if value == nil {
			invalid["sport_association"] = []string{PaymentCategoryValidationNull}
		} else if id, code := supplierRelated(value); code != "" {
			invalid["sport_association"] = []string{code}
		} else {
			update.Association, update.AssociationRaw = &id, value
		}
	}
	if value, present := object.Lookup("date"); present {
		update.DatePresent = true
		if value == nil {
			invalid["date"] = []string{PaymentCategoryValidationNull}
		} else if date, ok := ParseSubscriptionMembershipDate(value); ok {
			update.Date = &date
		} else {
			invalid["date"] = []string{ArchiveUpdateDate}
		}
	}
	if value, present := object.Lookup("folder"); present {
		update.FolderPresent = true
		if value != nil {
			pk, code, err := FolderPKValue(value)
			if err != nil {
				return update, nil, domain.ErrDocumentWritesNotPorted
			}
			if code != "" {
				invalid["folder"] = []string{code}
			} else {
				update.Folder = &pk
			}
		}
	}
	return update, invalid, nil
}
