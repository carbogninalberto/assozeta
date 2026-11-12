package service

import "github.com/carbogninalberto/assozeta/BE_GO/internal/domain"

// PersonaSentGroup is the created instance as Associate.objects.create left
// it: the group is the X-Group-Id text as sent (the serializer reads group_id
// without a lookup), where the stored column holds the canonical uuid.
func PersonaSentGroup(body any, header string) any {
	object, ok := body.(domain.RequestObject)
	group := supplierGroup(header)
	if !ok || group == nil || *group == header {
		return body
	}
	for i, field := range object {
		if field.Name == "group" && field.Value == *group {
			object[i].Value = header
		}
	}
	return object
}
