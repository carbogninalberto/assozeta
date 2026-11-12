package dto

import "encoding/json"

type MembershipCardConfiguration struct {
	EmitOnlyOnApproval bool            `json:"emit_only_on_approval"`
	CustomizedTemplate json.RawMessage `json:"customized_template"`
}
