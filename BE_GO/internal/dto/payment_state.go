package dto

import "github.com/carbogninalberto/assozeta/BE_GO/internal/domain"

// PaymentStateItem is PaymentSerializer for one payment: the shared payment
// rendering plus the nested InstructorSerializer and InvoiceSerializer.
func PaymentStateItem(row domain.PaymentStateRender) (map[string]any, error) {
	item := courseSubscriptionPayment(row.Payment)
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
