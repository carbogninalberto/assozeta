package dto

import (
	"encoding/json"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// InvoiceCoreLookupIDs is the right-hand side of invoice_id__in: an iterable
// (a string iterates its characters, a dict its keys) whose None values are
// dropped and whose other values go through the model UUIDField's to_python
// (ints and booleans as UUID(int=...)). ok=false is the exception Django
// raises (ValidationError, TypeError or ValueError: a 500).
func InvoiceCoreLookupIDs(value any) (ids []string, ok bool) {
	var items []any
	switch typed := value.(type) {
	case string:
		for _, r := range typed {
			items = append(items, string(r))
		}
	case []any:
		items = typed
	case domain.RequestObject:
		for _, field := range typed {
			items = append(items, field.Name)
		}
	default:
		return nil, false
	}
	seen := map[string]bool{}
	ids = []string{}
	for _, item := range items {
		switch typed := item.(type) {
		case nil:
			continue
		case bool:
			item = json.Number("0")
			if typed {
				item = json.Number("1")
			}
		case []any, domain.RequestObject:
			return nil, false // unhashable: TypeError
		}
		id, valid := supplierUUID(item)
		if !valid {
			return nil, false
		}
		if !seen[id] {
			seen[id] = true
			ids = append(ids, id)
		}
	}
	return ids, true
}

// InvoiceCoreInvoice is InvoiceSerializer(invoice).data.
func InvoiceCoreInvoice(row domain.PaymentReadsInvoice) (map[string]any, error) {
	return paymentReadsInvoice(row)
}

// InvoiceCoreListPayment is PaymentInvoiceSerializer(payment).data.
func InvoiceCoreListPayment(row domain.InvoiceCoreListPayment) map[string]any {
	payment := row.Payment
	var user, associate, supplier any
	if payment.User != nil {
		user = map[string]any{"first_name": payment.User.FirstName, "last_name": payment.User.LastName, "email": payment.User.Email,
			"phone": payment.User.Phone, "username": payment.User.Username, "role": payment.User.Role}
	}
	if row.Associate != nil {
		associate = paymentReadsAssociate(*row.Associate, row.Relations, row.Second, row.Third)
	}
	if payment.Supplier != nil {
		supplier = SupplierItemFrom(*payment.Supplier)
	}
	return map[string]any{"user": user, "associate": associate, "supplier": supplier, "subscription_id": payment.Subscription,
		"payment_date": invoicePartyTime(payment.PaymentDate), "course": invoicePartyJSON(payment.Course)}
}

// InvoiceCoreArchivedPayment is PaymentSerializer(payment).data.
func InvoiceCoreArchivedPayment(row domain.InvoiceCoreArchivedPayment) (map[string]any, error) {
	item := courseSubscriptionPayment(row.CourseSubscriptionPayment)
	if row.Instructor != nil {
		instructor, err := InstructorItemFrom(*row.Instructor)
		if err != nil {
			return nil, err
		}
		item["instructor"], item["instructor_id"] = instructor, row.Instructor.ID
	}
	if row.Invoice != nil {
		invoice, err := paymentReadsInvoice(*row.Invoice)
		if err != nil {
			return nil, err
		}
		item["invoice"] = invoice
	}
	return item, nil
}
