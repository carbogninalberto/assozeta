package dto

import (
	"encoding/json"
	"errors"
	"strings"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// ErrAssociationExportDocumentInvalid is UUIDField.to_python's ValidationError
// during the archive lookup: Django does not handle it (a 500 page).
var ErrAssociationExportDocumentInvalid = errors.New("export document_id is not a UUID")

// AssociationExportDocument is UUIDField.to_python for document_id: an int
// (bool included) is uuid.UUID(int=n), a string is parsed as hex; anything
// else is the unhandled ValidationError. Strings Python's int(hex, 16) reads
// differently from a plain hex check (underscores, signs, spaces) are refused.
func AssociationExportDocument(value any) (string, error) {
	switch v := value.(type) {
	case bool:
		if v {
			return "00000000-0000-0000-0000-000000000001", nil
		}
		return "00000000-0000-0000-0000-000000000000", nil
	case string:
		if strings.ContainsAny(v, "_+ \t\r\n\f\v") {
			return "", domain.ErrAssociationTransferNotPorted
		}
	case json.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return "", ErrAssociationExportDocumentInvalid
		}
	default:
		return "", ErrAssociationExportDocumentInvalid
	}
	id, ok := supplierUUID(value)
	if !ok {
		return "", ErrAssociationExportDocumentInvalid
	}
	return id, nil
}
