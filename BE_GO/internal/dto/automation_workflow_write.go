package dto

import (
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
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

// ErrAutomationWorkflowFormTreeNotPorted answers the stable 500 page for a
// form-encoded automation_tree whose parsed list the database column rejects
// (lone surrogate escapes, nesting past the parser limit): the save fails before any row changes, as it does in Django.
var ErrAutomationWorkflowFormTreeNotPorted = refusal.New("form-encoded automation_tree the jsonb column rejects")

// automationFormTree is DRF's JSONField.get_value/to_internal_value for an HTML
// form value: json.loads of the text. ok=false is "Value must be valid JSON."
// (a 400); rejected=true is a document Python parses but the jsonb column
// refuses at save time.
func automationFormTree(value any) (tree any, ok bool, rejected bool) {
	text, isText := value.(string)
	if !isText {
		return nil, false, false
	}
	if auditLoneSurrogate(text) || auditJSONDepth(text) > 500 {
		return []any{}, true, true
	}
	tree, err := parseReferenceJSON([]byte(text), nil, true)
	if err == nil {
		return tree, true, false
	}
	if errors.Is(err, ErrReferenceNesting) {
		return []any{}, true, true
	}
	if errors.Is(err, ErrLoginRateJSONValue) {
		return nil, false, false
	}
	return nil, false, false
}

// AutomationWorkflowPatch holds the present fields of a partial
// AutomationWorkflowSerializer; nil pointers are absent.
type AutomationWorkflowPatch struct {
	ID          *string
	Association *string // a well-formed pk; existence and tenant are checked by the service
	Name        *string
	Description **string
	Tree        *any
	// TreeRejected: the list passed validation but the jsonb column refuses it.
	TreeRejected bool
	Enabled      *bool
}

// ParseAutomationWorkflowPatch ports AutomationWorkflowSerializer(partial=True)
// field validation. invalid reports any field error (the view's generic 400);
// the relation is still parsed because a foreign association escapes as 403
// whatever the other errors are.
func ParseAutomationWorkflowPatch(body any, form bool) (AutomationWorkflowPatch, bool, error) {
	var patch AutomationWorkflowPatch
	object, ok := body.(domain.RequestObject)
	if !ok {
		return patch, true, nil
	}
	invalid := false
	if value, present := object.Lookup("automation_workflow_id"); present {
		if id, err := parsePaymentCategoryUUID(value); err != nil {
			invalid = true
		} else {
			patch.ID = &id
		}
	}
	if value, present := object.Lookup("sport_association"); present {
		if id, code := supplierRelated(value); code != "" || value == nil {
			invalid = true
		} else {
			patch.Association = &id
		}
	}
	if value, present := object.Lookup("name"); present {
		if name, codes := modelTextValue(SupplierField{Name: "name", Max: 255}, value, form); len(codes) > 0 || name == nil {
			invalid = true
		} else {
			patch.Name = name
		}
	}
	if value, present := object.Lookup("description"); present {
		if text, codes := modelTextValue(SupplierField{Name: "description", Null: true, Blank: true}, value, form); len(codes) > 0 {
			invalid = true
		} else {
			patch.Description = &text
		}
	}
	if value, present := object.Lookup("automation_tree"); present {
		rejected := false
		if form {
			parsed, valid, refused := automationFormTree(value)
			if !valid {
				invalid = true
				value = nil
			} else {
				value, rejected = parsed, refused
			}
		}
		tree, isList := value.([]any)
		if !isList {
			invalid = true
		} else {
			for _, raw := range tree {
				if node, isObject := raw.(domain.RequestObject); isObject {
					id, _ := node.Lookup("id")
					kind, _ := node.Lookup("value")
					if id == "message" && kind != "email" {
						invalid = true
					}
				}
			}
			var stored any = tree
			patch.Tree = &stored
			patch.TreeRejected = rejected
		}
	}
	if value, present := object.Lookup("enabled"); present {
		if enabled, err := parsePaymentCategoryBoolean(value); value == nil || err != nil {
			invalid = true
		} else {
			patch.Enabled = &enabled
		}
	}
	return patch, invalid, nil
}
