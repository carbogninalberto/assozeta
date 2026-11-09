// Package importeffects reproduces what Django does when the association
// import (BE/application/services/import_service.py) creates or changes a
// model row through the ORM: model(**kwargs).save() with field defaults and
// pre_save hooks, the save() overrides of Payment, Subscription and Course,
// django-mptt's insertion of Folder nodes, the raw save of message history,
// save(update_fields=...), QuerySet.update(), ManyRelatedManager.set(), and
// for every audited model the django-auditlog entry (create or update diff,
// object_repr) with its AuditLogIndex row (create_audit_log_index, the
// application's only post_save receiver that a plain save reaches).
//
// plan.json is generated from Django by
// TESTS/adapters/go/association_import_plan.py: the concrete fields of every
// imported model with their kinds, defaults, auto_now flags and foreign-key
// targets, the auditlog registration and what its create diff renders for
// reverse accessors, the many-to-many through tables, the __str__ and save()
// implementations in effect, and the importer's own tables (IMPORT_ORDER,
// PK_FIELDS, FK_MAPPINGS). Inputs outside what this package reproduces return
// ErrUnsupported before the statement that would need them.
package importeffects

import (
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
)

//go:embed plan.json
var planJSON []byte

// ErrUnsupported marks a value or path outside the reproduced subset; callers
// hand the work to Django (rolling back whatever they wrote).
var ErrUnsupported = errors.New("importeffects: outside the reproduced subset")

func unsupported(format string, args ...any) error {
	return fmt.Errorf("%w: "+format, append([]any{ErrUnsupported}, args...)...)
}

// Default is a model field's default: none (Field.get_default's empty value),
// a constant with its Python type, or a callable's dotted name.
type Default struct {
	Kind  string          `json:"kind"`
	Name  string          `json:"name"`
	Type  string          `json:"type"`
	Value json.RawMessage `json:"value"`
}

// Field is one concrete model field.
type Field struct {
	Name          string  `json:"name"`
	Attname       string  `json:"attname"`
	Column        string  `json:"column"`
	Null          bool    `json:"null"`
	DBType        string  `json:"db_type"`
	Default       Default `json:"default"`
	Type          string  `json:"type"`
	PrimaryKey    bool    `json:"primary_key"`
	Kind          string  `json:"kind"`
	Parsed        bool    `json:"parsed"`
	MaxDigits     int     `json:"max_digits"`
	DecimalPlaces int     `json:"decimal_places"`
	AutoNow       bool    `json:"auto_now"`
	AutoNowAdd    bool    `json:"auto_now_add"`
	Encoder       *string `json:"encoder"`
	MaxLength     *int    `json:"max_length"`
	TargetModel   string  `json:"target_model"`
	TargetApp     string  `json:"target_app"`
	TargetType    string  `json:"target_type"`
	TargetKind    string  `json:"target_kind"`
	Mapped        bool    `json:"mapped"`
	OneToOne      bool    `json:"one_to_one"`
	SelfReference bool    `json:"self_reference"`
}

// IsFK reports a ForeignKey or OneToOneField.
func (f *Field) IsFK() bool { return f.Kind == "fk" }

// ManyToOne is field.many_to_one (a ForeignKey, not a OneToOneField).
func (f *Field) ManyToOne() bool { return f.Kind == "fk" && !f.OneToOne }

// Audit is the auditlog registration of a model.
type Audit struct {
	Include []string          `json:"include"`
	Exclude []string          `json:"exclude"`
	Reverse map[string]string `json:"reverse"`
}

// M2M is a many-to-many field and its auto-created through table.
type M2M struct {
	Name        string `json:"name"`
	Table       string `json:"table"`
	Source      string `json:"source"`
	Target      string `json:"target"`
	TargetModel string `json:"target_model"`
	TargetTable string `json:"target_table"`
	TargetPK    string `json:"target_pk"`
	TargetKind  string `json:"target_kind"`
	AutoCreated bool   `json:"auto_created"`
}

// PK is the model's primary key.
type PK struct {
	Name    string `json:"name"`
	Attname string `json:"attname"`
	Column  string `json:"column"`
	Kind    string `json:"kind"`
}

// Model is one imported model.
type Model struct {
	Name          string   `json:"name"`
	App           string   `json:"app"`
	ModelName     string   `json:"model"`
	Table         string   `json:"table"`
	PK            PK       `json:"pk"`
	Fields        []*Field `json:"fields"`
	Audit         *Audit   `json:"audit"`
	M2M           []*M2M   `json:"m2m"`
	Str           string   `json:"str"`
	Save          string   `json:"save"`
	SelfReference bool     `json:"self_reference"`
	IsMPTT        bool     `json:"is_mptt"`

	byName, byAttname map[string]*Field
}

// Field returns the field named name (Django's field.name).
func (m *Model) Field(name string) *Field { return m.byName[name] }

// FieldByAttname returns the field whose attname is name.
func (m *Model) FieldByAttname(name string) *Field { return m.byAttname[name] }

// M2MField returns the many-to-many field named name.
func (m *Model) M2MField(name string) *M2M {
	for _, field := range m.M2M {
		if field.Name == name {
			return field
		}
	}
	return nil
}

// PKField is the primary key's Field.
func (m *Model) PKField() *Field { return m.byAttname[m.PK.Attname] }

// OrderEntry is one IMPORT_ORDER item.
type OrderEntry struct {
	File  string `json:"file"`
	Model string `json:"model"`
}

// Plan is plan.json.
type Plan struct {
	Format             int                    `json:"format"`
	Order              []OrderEntry           `json:"order"`
	PKFields           map[string]string      `json:"pk_fields"`
	FKMappings         map[string][][2]string `json:"fk_mappings"`
	DeprecatedSections map[string]string      `json:"deprecated_sections"`
	Settings           map[string]any         `json:"settings"`
	Models             []*Model               `json:"models"`

	byName map[string]*Model
}

// Model returns the plan of the model class named name.
func (p *Plan) Model(name string) *Model { return p.byName[name] }

// LoadPlan decodes the embedded plan.
func LoadPlan() (*Plan, error) {
	plan := &Plan{}
	if err := json.Unmarshal(planJSON, plan); err != nil {
		return nil, err
	}
	if plan.Format != 1 || len(plan.Models) == 0 || len(plan.Order) == 0 {
		return nil, errors.New("importeffects: empty or unknown plan")
	}
	plan.byName = map[string]*Model{}
	for _, model := range plan.Models {
		model.byName, model.byAttname = map[string]*Field{}, map[string]*Field{}
		for _, field := range model.Fields {
			model.byName[field.Name] = field
			model.byAttname[field.Attname] = field
		}
		if model.PKField() == nil {
			return nil, fmt.Errorf("importeffects: %s has no primary key field", model.Name)
		}
		plan.byName[model.Name] = model
	}
	for _, entry := range plan.Order {
		if plan.byName[entry.Model] == nil {
			return nil, fmt.Errorf("importeffects: %s missing from the plan", entry.Model)
		}
	}
	return plan, nil
}
