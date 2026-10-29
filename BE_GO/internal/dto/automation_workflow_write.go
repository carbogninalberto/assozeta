package dto

import (
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// ErrAutomationWorkflowInvalid is the view's generic {"msg": "workflow not valid."}.
var ErrAutomationWorkflowInvalid = errors.New("workflow not valid.")

// ErrAutomationWorkflowBody mirrors the view assigning into request.data:
// non-object JSON raises TypeError and form QueryDicts are immutable (HTTP 500).
var ErrAutomationWorkflowBody = errors.New("workflow body cannot be updated")

type AutomationWorkflowWrite struct {
	ID          *string
	Name        string
	Description *string
	Tree        any // validated list (RequestObject/[]any values)
	Enabled     bool
}

// ParseAutomationWorkflowWrite ports AutomationWorkflowSerializer(fields='__all__')
// for creation. Every validation error collapses into one generic 400.
func ParseAutomationWorkflowWrite(body any, form bool) (AutomationWorkflowWrite, error) {
	var input AutomationWorkflowWrite
	object, ok := body.(domain.RequestObject)
	if form || !ok {
		return input, ErrAutomationWorkflowBody
	}
	if value, present := object.Lookup("automation_workflow_id"); present {
		id, err := parsePaymentCategoryUUID(value)
		if err != nil {
			return input, ErrAutomationWorkflowInvalid
		}
		input.ID = &id
	}
	value, present := object.Lookup("name")
	if !present {
		return input, ErrAutomationWorkflowInvalid
	}
	name, codes := parsePaymentCategoryString(value, 255)
	if len(codes) > 0 {
		return input, ErrAutomationWorkflowInvalid
	}
	input.Name = name
	if value, present := object.Lookup("description"); present && value != nil {
		text, isString := value.(string)
		if isString && pythonStrip(text) == "" {
			empty := ""
			input.Description = &empty
		} else if text, codes := parsePaymentCategoryString(value, 1<<31-1); len(codes) > 0 {
			return input, ErrAutomationWorkflowInvalid
		} else {
			input.Description = &text
		}
	}
	input.Tree = []any{}
	if value, present := object.Lookup("automation_tree"); present {
		tree, isList := value.([]any)
		if !isList {
			return input, ErrAutomationWorkflowInvalid
		}
		for _, raw := range tree {
			if node, isObject := raw.(domain.RequestObject); isObject {
				id, _ := node.Lookup("id")
				kind, _ := node.Lookup("value")
				if id == "message" && kind != "email" {
					return input, ErrAutomationWorkflowInvalid
				}
			}
		}
		input.Tree = tree
	}
	if value, present := object.Lookup("enabled"); present {
		if value == nil {
			return input, ErrAutomationWorkflowInvalid
		}
		enabled, err := parsePaymentCategoryBoolean(value)
		if err != nil {
			return input, ErrAutomationWorkflowInvalid
		}
		input.Enabled = enabled
	}
	return input, nil
}
