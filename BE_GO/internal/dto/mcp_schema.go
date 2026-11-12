package dto

import (
	"encoding/json"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// application/mcp_server/schema.py over the generated model metadata.

var mcpFieldTypes = map[string]string{"AutoField": "integer", "BigAutoField": "integer", "SmallAutoField": "integer",
	"UUIDField": "uuid", "CharField": "string", "TextField": "text", "IntegerField": "integer", "SmallIntegerField": "integer",
	"BigIntegerField": "integer", "PositiveIntegerField": "integer", "PositiveSmallIntegerField": "integer", "FloatField": "float",
	"DecimalField": "decimal", "BooleanField": "boolean", "NullBooleanField": "boolean", "DateField": "date",
	"DateTimeField": "datetime", "TimeField": "time", "EmailField": "email", "URLField": "url", "FileField": "file",
	"ImageField": "image", "JSONField": "json", "BinaryField": "binary", "DurationField": "duration",
	"GenericIPAddressField": "ip_address", "SlugField": "string", "FilePathField": "string"}

var mcpRelationTypes = map[string]string{"ForeignKey": "foreign_key", "OneToOneField": "one_to_one", "ManyToManyField": "many_to_many",
	"ManyToManyRel": "many_to_many_reverse", "ManyToOneRel": "foreign_key_reverse", "OneToOneRel": "one_to_one_reverse"}

func mcpObject(pairs ...any) domain.RequestObject {
	object := make(domain.RequestObject, 0, len(pairs)/2)
	for i := 0; i < len(pairs); i += 2 {
		object = append(object, domain.RequestField{Name: pairs[i].(string), Value: pairs[i+1]})
	}
	return object
}

// mcpJSONFieldSchemas is JSON_FIELD_SCHEMAS.
var mcpJSONFieldSchemas = map[string]domain.RequestObject{
	"AttendanceRegistry.events": mcpObject("description", "Lista di eventi/lezioni del corso con date e orari", "type", "array",
		"items", `{"event_id": "UUID", "title": "string", "start": "ISO datetime", "end": "ISO datetime", "allDay": bool, `+
			`"extendedProps": {"description": "string", "instructor": {"instructor_id": "UUID", "label": "string"}}}`),
	"AttendanceDay.attendees": mcpObject("description", "Lista dei presenti alla lezione. Ogni elemento ha un course_subscription_id. "+
		"Se un iscritto al corso e' presente in questa lista, era PRESENTE alla lezione.", "type", "array",
		"items", `{"course_subscription_id": "UUID"}`),
	"AttendanceDay.expected_absences": mcpObject("description", "Lista delle assenze previste. Stessa struttura di attendees. "+
		"Se un iscritto e' qui, era ASSENTE alla lezione.", "type", "array", "items", `{"course_subscription_id": "UUID"}`),
	"CarnetSubscription.meta": mcpObject("description", "Dati del carnet: lezioni rimanenti e storico utilizzo. "+
		"Usa meta__lessons_left per filtrare i carnet con lezioni disponibili.", "type", "object",
		"items", `{"lessons_left": int, "lessons_registry": [{"date": "ISO", "attendance_day_id": "UUID", "course": {"id": "UUID", "title": "string"}}]}`),
}

const mcpSchemaNote = "Tutte le query sono automaticamente filtrate per la tua associazione sportiva. " +
	"Per accedere a campi di modelli collegati via FK, usa la notazione doppio underscore: " +
	"ad esempio, su CourseSubscription usa subscription__associate__first_name per il nome del tesserato. " +
	"Ogni relazione ha un campo \"usage\" che mostra il prefisso corretto da usare."

// mcpSchemaField is _get_field_info (nil: blacklisted).
func mcpSchemaField(field *mcpField, model string) (domain.RequestObject, error) {
	if !reportAllowed(field.Name) {
		return nil, nil
	}
	mapped, ok := mcpFieldTypes[field.Class]
	if !ok {
		mapped = field.Class
	}
	verbose := field.Name
	if field.VerboseName != nil {
		verbose = *field.VerboseName
	}
	info := mcpObject("name", field.Name, "type", mapped, "verbose_name", verbose)
	if field.HelpText != "" {
		info = append(info, domain.RequestField{Name: "help_text", Value: field.HelpText})
	}
	if field.GroupedChoices {
		return nil, mcpNotPorted("grouped choices")
	}
	if len(field.Choices) > 0 {
		choices := make([]any, 0, len(field.Choices))
		for _, choice := range field.Choices {
			choices = append(choices, mcpObject("value", choice.Value, "label", choice.Label))
		}
		info = append(info, domain.RequestField{Name: "choices", Value: choices})
	}
	info = append(info, domain.RequestField{Name: "is_required", Value: !field.Blank && !field.Null && !field.HasDefault})
	if field.MaxLength != nil && *field.MaxLength != "0" {
		info = append(info, domain.RequestField{Name: "max_length", Value: *field.MaxLength})
	}
	if mapped == "json" && model != "" {
		if structure, ok := mcpJSONFieldSchemas[model+"."+field.Name]; ok {
			info = append(info, domain.RequestField{Name: "json_structure", Value: structure})
		}
	}
	return info, nil
}

// mcpSchemaRelation is _get_relation_info (nil: not a mapped relation type).
func mcpSchemaRelation(field *mcpField) domain.RequestObject {
	relation, ok := mcpRelationTypes[field.Class]
	related := field.relatedModel()
	if !ok || related == nil {
		return nil
	}
	info := mcpObject("name", field.Name, "relation_type", relation, "related_model", related.Label)
	if field.SchemaRelatedName != nil && *field.SchemaRelatedName != "" {
		info = append(info, domain.RequestField{Name: "related_name", Value: *field.SchemaRelatedName})
	}
	if field.SchemaVerboseName != nil && *field.SchemaVerboseName != "" {
		info = append(info, domain.RequestField{Name: "verbose_name", Value: *field.SchemaVerboseName})
	}
	if relation == "foreign_key" || relation == "one_to_one" {
		info = append(info, domain.RequestField{Name: "usage", Value: mcpObject(
			"id_field", field.Name+"_id",
			"traverse_prefix", field.Name+"__",
			"example", field.Name+"__id (or "+field.Name+"__<any_field_of_"+related.Name+">)")})
	}
	return info
}

// mcpModelSchema is get_model_schema: only the model's listed fields and
// relations (a model without a list has none).
func mcpModelSchema(model *mcpModel) (domain.RequestObject, error) {
	fields, relations := []any{}, []any{}
	for _, field := range model.Fields {
		if field.Relation {
			if !model.reportRelationListed(field.Name) {
				continue
			}
			if info := mcpSchemaRelation(field); info != nil {
				relations = append(relations, info)
			}
			continue
		}
		if !model.reportFieldListed(field.Name) {
			continue
		}
		info, err := mcpSchemaField(field, model.Name)
		if err != nil {
			return nil, err
		}
		if info != nil {
			fields = append(fields, info)
		}
	}
	return mcpObject("model_name", model.Name, "app_label", "application", "full_label", model.Label,
		"verbose_name", model.VerboseName, "verbose_name_plural", model.VerbosePlural, "fields", fields, "relations", relations), nil
}

// MCPSchema is tool_get_schema(model_name=...).
func MCPSchema(value any) (ReportPlan, error) {
	if !PythonTruthy(value) || value == "null" {
		models := []any{}
		for _, entry := range mcpWhitelist {
			schema, err := mcpModelSchema(mcpModelByLabel(entry.Label))
			if err != nil {
				return ReportPlan{}, err
			}
			models = append(models, schema)
		}
		return ReportPlan{Value: mcpObject("models", models, "note", mcpSchemaNote)}, nil
	}
	var text string
	switch v := value.(type) {
	case string:
		text = v
	case bool, json.Number:
		text = PythonStr(v)
	case domain.RequestObject:
		return ReportPlan{}, mcpRaise("unhashable type: 'dict'")
	case []any:
		return ReportPlan{}, mcpRaise("unhashable type: 'list'")
	default:
		return ReportPlan{}, mcpNotPorted("model_name type")
	}
	for _, candidate := range []string{"application." + text, text} {
		if _, isText := value.(string); !isText && candidate == text {
			continue // get_model_schema(non-string) is never a whitelist key
		}
		for _, entry := range mcpWhitelist {
			if entry.Label == candidate {
				schema, err := mcpModelSchema(mcpModelByLabel(entry.Label))
				if err != nil {
					return ReportPlan{}, err
				}
				return ReportPlan{Value: schema}, nil
			}
		}
	}
	return ReportPlan{Immediate: &ReportToolError{Message: "Model " + text + " not found"}}, nil
}
