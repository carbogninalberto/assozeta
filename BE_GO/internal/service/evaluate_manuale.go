package service

import "github.com/carbogninalberto/assozeta/BE_GO/internal/domain"

// EvaluateManualeGroundedAnswer is application.manuale.answers.grounded_answer
// for the evaluate_manuale command's deterministic answer fixtures.
func EvaluateManualeGroundedAnswer(result domain.RequestObject) (string, error) {
	return agentGroundedAnswer(result)
}

// EvaluateManualeSectionAttachment is manual_section_attachment (nil is None).
func EvaluateManualeSectionAttachment(result domain.RequestObject) (any, error) {
	return agentManualAttachment(result)
}
