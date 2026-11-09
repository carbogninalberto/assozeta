// Package associationexport reproduces AssociationExportService
// (BE/application/services/export_service.py): the per-model JSON files of an
// association export, byte for byte.
//
// plan.json is generated from Django by
// TESTS/adapters/go/association_export_plan.py: for every EXPORT_ORDER entry
// the keys serialize_record writes (in _meta.get_fields() order, exclusions
// applied), the export queryset's SQL as Django compiles it (placeholder ids
// bound to the association, its owner or the collected document ids) and the
// prefetch query of each M2M_FIELDS relation. Running Django's own SQL keeps
// PostgreSQL's plans, and so the order of rows without ORDER BY, identical.
package associationexport

import (
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
)

//go:embed plan.json
var planJSON []byte

// Param is one SQL parameter: a symbol (association, owner), a literal, the
// collected document ids or the prefetch sources (both expand an IN list).
type Param struct {
	Symbol    string `json:"symbol,omitempty"`
	Bool      *bool  `json:"bool,omitempty"`
	Int       *int64 `json:"int,omitempty"`
	Str       *string
	Documents bool `json:"documents,omitempty"`
	Sources   bool `json:"sources,omitempty"`
}

func (p *Param) UnmarshalJSON(data []byte) error {
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		return err
	}
	type plain Param
	var value plain
	if err := json.Unmarshal(data, &value); err != nil {
		return err
	}
	*p = Param(value)
	if text, ok := raw["str"]; ok {
		var s string
		if err := json.Unmarshal(text, &s); err != nil {
			return err
		}
		p.Str = &s
	}
	return nil
}

// Query is Django's compiled SQL with %s placeholders.
type Query struct {
	SQL    string  `json:"sql"`
	Params []Param `json:"params"`
}

// Field is one serialize_record key.
type Field struct {
	Key    string `json:"key"`
	Column string `json:"column"`
	// Kind: fk (str(value) if value else None), null (a reverse one-to-one:
	// always None), uuid, decimal, datetime, date, time, json, bool, text, int.
	Kind   string `json:"kind"`
	Target string `json:"target"`
}

// M2M is one prefetched many-to-many relation.
type M2M struct {
	Key          string  `json:"key"`
	SQL          string  `json:"sql"`
	Params       []Param `json:"params"`
	SourceColumn string  `json:"source_column"`
	TargetPK     string  `json:"target_pk"`
}

// Model is one EXPORT_ORDER entry.
type Model struct {
	Name               string   `json:"model"`
	File               string   `json:"file"`
	Fields             []Field  `json:"fields"`
	Table              string   `json:"table"`
	PK                 string   `json:"pk"`
	PKKind             string   `json:"pk_kind"`
	Query              Query    `json:"query"`
	Columns            []string `json:"columns"`
	M2M                []M2M    `json:"m2m"`
	DocumentAttributes []string `json:"document_attributes"`
}

// Plan is the generated export plan.
type Plan struct {
	Format   int                 `json:"format"`
	Excluded map[string][]string `json:"excluded"`
	Models   []Model             `json:"models"`
	Queries  struct {
		Cleanup          Query    `json:"cleanup"`
		CleanupExcess    Query    `json:"cleanup_excess"`
		Files            Query    `json:"files"`
		Signatures       Query    `json:"signatures"`
		Credentials      Query    `json:"credentials"`
		SignatureColumns []string `json:"signature_columns"`
	} `json:"queries"`
}

// documentM2M are the relations _collect_document_references adds.
var documentM2M = map[string]bool{"_m2m_documents": true, "_m2m_attachments": true, "_m2m_attached_documents": true,
	"_m2m_attached_membership_documents": true}

// LoadPlan decodes the embedded plan.
func LoadPlan() (*Plan, error) {
	var plan Plan
	if err := json.Unmarshal(planJSON, &plan); err != nil {
		return nil, err
	}
	if plan.Format != 1 || len(plan.Models) == 0 {
		return nil, errors.New("associationexport: invalid plan")
	}
	for _, model := range plan.Models {
		if model.Name == "" || model.File == "" || len(model.Columns) == 0 || model.Query.SQL == "" {
			return nil, fmt.Errorf("associationexport: incomplete plan for %q", model.Name)
		}
		for _, relation := range model.M2M {
			if strings.Count(relation.SQL, "IN (%s, %s)") != 1 {
				return nil, fmt.Errorf("associationexport: prefetch of %s.%s", model.Name, relation.Key)
			}
		}
	}
	return &plan, nil
}
