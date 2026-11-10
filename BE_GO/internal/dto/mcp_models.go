package dto

import (
	"bytes"
	_ "embed"
	"encoding/json"
	"sort"
	"strings"
)

// Model metadata of the MCP query engine, generated from Django's _meta by
// TESTS/fixtures/generate_mcp_server_models.py: every model the whitelist
// reaches through relations, fields in get_fields() order, the whitelisted
// models' default manager filters and the Italian validation templates.

//go:embed mcp_models.json
var mcpModelsJSON []byte

type mcpChoice struct {
	Value any // json.Number or string, as the Python choice key
	Label string
}

type mcpField struct {
	Name          string          `json:"name"`
	Class         string          `json:"class"`
	Relation      bool            `json:"relation"`
	Concrete      bool            `json:"concrete"`
	Related       string          `json:"related"`
	Attname       string          `json:"attname"`
	Column        string          `json:"column"`
	Internal      string          `json:"internal"`
	Null          bool            `json:"null"`
	Blank         bool            `json:"blank"`
	HasDefault    bool            `json:"has_default"`
	MaxLength     *json.Number    `json:"max_length"`
	VerboseName   *string         `json:"verbose_name"`
	HelpText      string          `json:"help_text"`
	MaxDigits     int             `json:"max_digits"`
	DecimalPlaces int             `json:"decimal_places"`
	RawChoices    json.RawMessage `json:"choices"`
	Kind          string          `json:"kind"`
	// Forward relations: the related column and its field type.
	TargetColumn   string  `json:"target_column"`
	TargetInternal string  `json:"target_internal"`
	TargetAttname  string  `json:"target_attname"`
	RelatedName    *string `json:"related_name"`
	// getattr(field, 'related_name'/'verbose_name', None) as schema.py reads them.
	SchemaRelatedName *string `json:"schema_related_name"`
	SchemaVerboseName *string `json:"schema_verbose_name"`
	// Reverse and many-to-many relations.
	Through      string `json:"through"`
	SourceColumn string `json:"source_column"`
	LocalColumn  string `json:"local_column"`
	RemoteColumn string `json:"remote_column"`

	Choices        []mcpChoice
	GroupedChoices bool
	model          *mcpModel
}

type mcpModel struct {
	Label          string
	Name           string      `json:"name"`
	Table          string      `json:"table"`
	PK             string      `json:"pk"`
	Ordering       []string    `json:"ordering"`
	VerboseName    string      `json:"verbose_name"`
	VerbosePlural  string      `json:"verbose_name_plural"`
	Fields         []*mcpField `json:"fields"`
	ManagerFilters []string    `json:"manager_filters"`
	// GroupManager: the default manager filters by the request's X-Group-Id.
	GroupManager bool `json:"group_manager"`
}

type mcpMetadata struct {
	Models   map[string]*mcpModel `json:"models"`
	Messages map[string]string    `json:"messages"`
}

var mcpMeta = func() *mcpMetadata {
	var meta mcpMetadata
	decoder := json.NewDecoder(bytes.NewReader(mcpModelsJSON))
	decoder.UseNumber()
	if err := decoder.Decode(&meta); err != nil {
		panic("mcp model metadata: " + err.Error())
	}
	for label, model := range meta.Models {
		model.Label = label
		for _, field := range model.Fields {
			field.model = model
			if len(field.RawChoices) == 0 || string(field.RawChoices) == "null" {
				continue
			}
			if string(field.RawChoices) == `"grouped"` {
				field.GroupedChoices = true
				continue
			}
			var pairs [][]any
			inner := json.NewDecoder(bytes.NewReader(field.RawChoices))
			inner.UseNumber()
			if err := inner.Decode(&pairs); err != nil {
				panic("mcp model choices: " + err.Error())
			}
			for _, pair := range pairs {
				field.Choices = append(field.Choices, mcpChoice{Value: pair[0], Label: pair[1].(string)})
			}
		}
	}
	return &meta
}()

// mcpWhitelist is security.MODEL_WHITELIST in declaration order with its
// tenant_field and soft_delete_field.
var mcpWhitelist = []struct {
	Label, Tenant string
	SoftDelete    []string
}{
	{"application.SportAssociation", "_self", []string{"deleted"}},
	{"application.Associate", "sport_association", []string{"deleted"}},
	{"application.Group", "sport_association", nil},
	{"application.Subscription", "sport_association", []string{"deleted", "archived"}},
	{"application.Course", "sport_association", []string{"deleted"}},
	{"application.CourseSubscription", "course__sport_association", []string{"deleted", "archived"}},
	{"application.Payment", "sport_association", []string{"deleted", "archived"}},
	{"application.PaymentCategory", "sport_association", []string{"archived"}},
	{"application.Invoice", "sport_association", []string{"deleted", "archived"}},
	{"application.InvoiceRows", "invoice__sport_association", nil},
	{"application.MedicalCertificate", "subscription__sport_association", nil},
	{"application.Instructor", "user__sportassociation", nil},
	{"application.BalanceSheet", "sport_association", []string{"archived"}},
	{"application.CustomAccounts", "sport_association", nil},
	{"application.Tags", "sport_association", nil},
	{"application.CourseTags", "sport_association", nil},
	{"application.Carnet", "sport_association", nil},
	{"application.CarnetSubscription", "carnet_id__sport_association", []string{"disabled"}},
	{"application.Module", "sport_association", nil},
	{"application.AttendanceRegistry", "course__sport_association", nil},
	{"application.AttendanceDay", "attendance_registry__course__sport_association", nil},
	{"application.Reminders", "sport_association", nil},
}

func mcpModelByLabel(label string) *mcpModel { return mcpMeta.Models[label] }

// getField is Options.get_field: a field's name, or a concrete field's attname.
func (m *mcpModel) getField(name string) *mcpField {
	for _, field := range m.Fields {
		if field.Name == name {
			return field
		}
	}
	for _, field := range m.Fields {
		if field.Concrete && field.Attname == name {
			return field
		}
	}
	return nil
}

func (m *mcpModel) pkField() *mcpField { return m.getField(m.PK) }

// fieldByColumn is the concrete field stored in column.
func (m *mcpModel) fieldByColumn(column string) *mcpField {
	for _, field := range m.Fields {
		if field.Concrete && field.Column == column {
			return field
		}
	}
	return nil
}

// choiceNames is get_field_names_from_opts sorted, the FieldError choices.
func (m *mcpModel) choiceNames() string {
	seen := map[string]bool{}
	var names []string
	for _, field := range m.Fields {
		candidates := []string{field.Name}
		if field.Concrete {
			candidates = append(candidates, field.Attname)
		}
		for _, name := range candidates {
			if !seen[name] {
				seen[name] = true
				names = append(names, name)
			}
		}
	}
	sort.Strings(names)
	return strings.Join(names, ", ")
}

// knownNames is the set _validate_fields_for_model accepts: every
// get_fields() name plus attnames that differ from the name.
func (m *mcpModel) known(name string) bool {
	for _, field := range m.Fields {
		if field.Name == name || (field.Attname != "" && field.Attname == name) {
			return true
		}
	}
	return false
}

func (f *mcpField) relatedModel() *mcpModel { return mcpModelByLabel(f.Related) }

// valueType is the internal type of the values the field stores (a forward
// relation stores its target's type).
func (f *mcpField) valueType() string {
	if f.Kind == "foreign_key" || f.Kind == "one_to_one" {
		return f.TargetInternal
	}
	return f.Internal
}

func (f *mcpField) isForwardRelation() bool { return f.Kind == "foreign_key" || f.Kind == "one_to_one" }
