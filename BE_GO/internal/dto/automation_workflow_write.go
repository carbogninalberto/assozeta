package dto

import (
	"encoding/json"
	"errors"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// ErrAutomationWorkflowInvalid is the view's generic {"msg": "workflow not valid."}.
var ErrAutomationWorkflowInvalid = errors.New("workflow not valid.")

// ErrAutomationWorkflowBody mirrors the view assigning into request.data:
// non-object JSON raises TypeError and form QueryDicts are immutable (HTTP 500).
var ErrAutomationWorkflowBody = errors.New("workflow body cannot be updated")

type AutomationWorkflowWrite struct {
	Name        string
	Description *string
	Tree        any // validated list (RequestObject/[]any values)
	Enabled     bool
}

// ParseAutomationWorkflowWrite ports AutomationWorkflowSerializer(fields='__all__')
// for creation. Every validation error collapses into one generic 400. The
// primary key is read-only: an automation_workflow_id in the body is ignored.
func ParseAutomationWorkflowWrite(body any, form bool) (AutomationWorkflowWrite, error) {
	var input AutomationWorkflowWrite
	object, ok := body.(domain.RequestObject)
	if form || !ok {
		return input, ErrAutomationWorkflowBody
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

// formJSONNesting is the parser limit of the HTML-form JSON fields (owner
// decision X15): the measured Python bands sit just below CPython's C recursion
// limit (10000), and this reader must parse every depth up to them. Deeper
// documents are still marked with their depth and answered by the callers'
// measured bands, never by the parser.
const formJSONNesting = 20000

// automationFormTree is DRF's JSONField.get_value/to_internal_value for an HTML
// form value: json.loads of the text. ok=false is "Value must be valid JSON."
// (a 400); rejected=true is a document Python parses (lone surrogate escapes
// are kept as code units) but the jsonb column refuses at save time; depth is
// the maximum nesting of the text (auditJSONDepth's D), used by the callers'
// measured recursion bands (X15).
func automationFormTree(value any) (tree any, ok, rejected bool, depth int) {
	text, isText := value.(string)
	if !isText {
		return nil, false, false, 0
	}
	depth = auditJSONDepth(text)
	tree, err := parseProfileTablesJSONConstantsLimit([]byte(text), map[int64]any{}, true, formJSONNesting)
	switch {
	case err == nil:
		return tree, true, auditLoneSurrogate(text), depth
	case errors.Is(err, ErrReferenceNesting):
		// Past even the reader's limit: keep the depth so the callers answer
		// their measured band, with an empty list they never store.
		return []any{}, true, false, depth
	}
	// json.loads stops at the first syntax error: only the nesting reached
	// before it can raise RecursionError, so a deeper tail after the error is
	// still "Value must be valid JSON.".
	var syntax *json.SyntaxError
	if errors.As(err, &syntax) && syntax.Offset >= 0 && syntax.Offset <= int64(len(text)) {
		depth = auditJSONDepth(text[:syntax.Offset])
	}
	return nil, false, false, depth
}

// ParseFormJSON is json.loads for an HTML form JSON value the serializer
// accepted: the same reader as automationFormTree (lone surrogate escapes kept
// as code units) at the parser limit that covers the measured bands (X15).
func ParseFormJSON(text string) (any, error) {
	return parseProfileTablesJSONConstantsLimit([]byte(text), map[int64]any{}, true, formJSONNesting)
}

// AutomationWorkflowPatch holds the present fields of a partial
// AutomationWorkflowSerializer; nil pointers are absent (the read-only
// automation_workflow_id is ignored).
type AutomationWorkflowPatch struct {
	Association *string // a well-formed pk; existence and tenant are checked by the service
	Name        *string
	Description **string
	Tree        *any
	// TreeRejected: the list passed validation but the jsonb column refuses it (lone surrogate escapes, or the
	// audit-only band of the measured form-tree depths).
	TreeRejected bool
	// TreeDepth is the maximum nesting of a form-encoded automation_tree (0 when
	// absent or sent as JSON); the service answers the measured bands (X15).
	TreeDepth int
	Enabled   *bool
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
			parsed, valid, refused, depth := automationFormTree(value)
			patch.TreeDepth = depth
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
