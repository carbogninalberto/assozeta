package dto

import "github.com/carbogninalberto/assozeta/BE_GO/internal/domain"

// CourseEnrolmentPaymentItem is PaymentSerializer(payment).data for a payment
// without instructor or invoice (the course-subscriptions rendering).
func CourseEnrolmentPaymentItem(row domain.CourseSubscriptionPayment) map[string]any {
	return courseSubscriptionPayment(row)
}
