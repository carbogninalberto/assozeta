package dto

import "github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"

// The application states of the MCP query engine: branches no request input can
// reach, because either the caller never dispatches there or the embedded model
// metadata (TESTS/fixtures/generate_mcp_server_models.py, asserted by
// mcp_models_invariants_test.go) guarantees the shape. They are answered as
// Go's own 500 page and never handed off to Django.
var (
	// errMCPToolDispatch: service/saved_reports.go RunGroup sends export_data,
	// export_multi_sheet and get_attendance_matrix to runFileTool before
	// PlanReportToolGroup, and SavedReportTools lists no other tool.
	errMCPToolDispatch = refusal.AppState("mcp: unknown report tool")

	// errMCPAttributeValue: mcpAttributeError only sees the parsed JSON values
	// (and the non-finite float64s the JSON parser produces for NaN/Infinity).
	errMCPAttributeValue = refusal.AppState("mcp: attribute error on an unexpected value type")

	// errMCPRelationOrdering: no relation in report_relations reaches a model
	// with Meta.ordering (only SavedReport, auditlog.LogEntry, auth.Permission
	// and communications.StaffBoardMessage have one).
	errMCPRelationOrdering = refusal.AppState("mcp: ordering by a relation with Meta.ordering")

	// errMCPNumberOperand: after mcpPyAdd read the offset as a number, and for
	// every parsed JSON value type mcpPyAdd either adds it or raises Python's
	// own TypeError.
	errMCPNumberOperand = refusal.AppState("mcp: limit or offset of an unexpected type")

	// errMCPUnexpectedOutput: Django's PostgreSQL output cannot leave these
	// branches (see each use).
	errMCPUnexpectedOutput = refusal.AppState("mcp: query output of an unexpected type")

	// errMCPGroupedChoices: no field in the metadata has grouped choices.
	errMCPGroupedChoices = refusal.AppState("mcp: choices grouped by a category")

	// errMCPRelationMetadata: every relation's related label names a model.
	errMCPRelationMetadata = refusal.AppState("mcp: relation without model metadata")

	// errMCPRelationTarget: a forward relation's target_column is a column of
	// the related model.
	errMCPRelationTarget = refusal.AppState("mcp: relation target column without a field")

	// errMCPRelationKind: every relation kind is one of the six the engine
	// handles.
	errMCPRelationKind = refusal.AppState("mcp: relation of an unexpected kind")

	// errMCPFieldKind: the metadata marks no field as unsupported.
	errMCPFieldKind = refusal.AppState("mcp: field of an unsupported kind")

	// errMCPFieldType: every reachable report field has a type mcpKind maps.
	errMCPFieldType = refusal.AppState("mcp: subject field of an unexpected type")

	// errMCPManyValuedExclude: splitExclude's inner query always holds the
	// many-valued join and every many-valued relation's local column is a field
	// of its model.
	errMCPManyValuedExclude = refusal.AppState("mcp: exclude across a many-valued relation")

	// errMCPSchemaModelName: get_schema only sees the parsed JSON values (and
	// the non-finite float64s the JSON parser produces for NaN/Infinity).
	errMCPSchemaModelName = refusal.AppState("mcp: get_schema model_name of an unexpected type")
)
