package service

import (
	"context"
	"encoding/json"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

// automationWorkflowUpdateValues renders auditlog's update strings of the
// fields it diffs in pre_save (updated_at is set later by auto_now, so it is
// never listed): JSON as sorted ASCII json.dumps, None as 'None'.
func automationWorkflowUpdateValues(row domain.AutomationWorkflowRecord, tree any) map[string]string {
	values := map[string]string{"automation_workflow_id": row.ID, "sport_association": row.AssociationID, "name": row.Name,
		"description": "None", "automation_tree": string(dto.PythonJSONDumpsSorted(tree)),
		"enabled": map[bool]string{true: "True", false: "False"}[row.Enabled], "created_at": "None"}
	if row.Description != nil {
		values["description"] = *row.Description
	}
	if row.CreatedAt != nil {
		values["created_at"] = paymentCategoryAuditTime(*row.CreatedAt)
	}
	return values
}

func automationWorkflowUpdateChanges(before, after map[string]string) map[string][2]string {
	changes := map[string][2]string{}
	for key, value := range after {
		if before[key] != value {
			changes[key] = [2]string{before[key], value}
		}
	}
	return changes
}

type automationWorkflowTriggerOutcome int

const (
	automationWorkflowTriggerNone automationWorkflowTriggerOutcome = iota
	automationWorkflowTriggerError
	// automationWorkflowTriggerSchedule is a future cron time: trigger() sends
	// check_workflows_trigger_cron with the countdown to it.
	automationWorkflowTriggerSchedule
)

// automationWorkflowPytzLastTransition is the last Europe/Rome transition in
// pytz's table (2037-10-25 01:00 UTC); pytz keeps that offset (+01:00) after it.
var automationWorkflowPytzLastTransition = time.Date(2037, 10, 25, 1, 0, 0, 0, time.UTC)

// automationWorkflowRomeLocalize is pytz's Europe/Rome localize(naive) with
// is_dst=False: the offset whose UTC instant reads back as the same wall
// time; an ambiguous or skipped wall time takes the standard offset (+01:00).
func automationWorkflowRomeLocalize(year, month, day, hour, minute, second, nanosecond int, rome *time.Location) time.Time {
	wall := time.Date(year, time.Month(month), day, hour, minute, second, nanosecond, time.UTC)
	offsetAt := func(instant time.Time) time.Duration {
		if !instant.Before(automationWorkflowPytzLastTransition) {
			return time.Hour
		}
		_, offset := instant.In(rome).Zone()
		return time.Duration(offset) * time.Second
	}
	standard, summer := wall.Add(-time.Hour), wall.Add(-2*time.Hour)
	if offsetAt(summer) == 2*time.Hour && offsetAt(standard) != time.Hour {
		return summer
	}
	return standard
}

// automationWorkflowEnablePlan follows the view when a save turns a workflow
// on: whether the try block resets a cron trigger's 'triggered' flag (a second
// save), and what trigger() then does with the tree. trigger() errors are a
// 500 after the saves (a time datetime.fromisoformat() or pytz's localize()
// raise on included); a future cron time is scheduled (at is the localized
// time).
func automationWorkflowEnablePlan(tree any, now time.Time, rome *time.Location) (reset bool, outcome automationWorkflowTriggerOutcome, at time.Time) {
	nodes, isList := tree.([]any)
	if !isList || len(nodes) == 0 {
		return false, automationWorkflowTriggerError, at
	}
	first, isObject := nodes[0].(domain.RequestObject)
	if !isObject {
		return false, automationWorkflowTriggerError, at
	}
	value, present := first.Lookup("value")
	if !present {
		return false, automationWorkflowTriggerError, at
	}
	data, hasData := first.Lookup("data")
	object, dataObject := data.(domain.RequestObject)
	if value == "cron" && hasData && dataObject {
		if triggered, found := object.Lookup("triggered"); found && triggered == true {
			reset = true
		}
	}
	switch {
	case value == nil:
		if len(nodes) > 1 {
			return reset, automationWorkflowTriggerError, at
		}
		return reset, automationWorkflowTriggerNone, at
	case value != "cron":
		return reset, automationWorkflowTriggerNone, at
	}
	if !dataObject {
		return reset, automationWorkflowTriggerError, at
	}
	raw, found := object.Lookup("time")
	text, isString := raw.(string)
	if !found || !isString {
		return reset, automationWorkflowTriggerError, at
	}
	parsed, valid := automationWorkflowFromISO(text)
	if !valid {
		return reset, automationWorkflowTriggerError, at
	}
	if parsed.year == 1 && parsed.month == 1 && parsed.day == 1 || parsed.year == 9999 && parsed.month == 12 && parsed.day == 31 {
		// localize() looks one day back and one day ahead: OverflowError.
		return reset, automationWorkflowTriggerError, at
	}
	at = automationWorkflowRomeLocalize(parsed.year, parsed.month, parsed.day, parsed.hour, parsed.minute, parsed.second,
		parsed.microsecond*1000, rome)
	// time_secs < 0: "Time is in the past", nothing scheduled.
	if at.Before(now) {
		return reset, automationWorkflowTriggerNone, at
	}
	return reset, automationWorkflowTriggerSchedule, at
}

// Update ports communication_workflows_update: lookup (404), partial
// validation (403 foreign relation, else 400), save, and on enabling the cron
// reset save and trigger(); not-ported paths are refused before any write.
func (s *AutomationWorkflowWrites) Update(ctx context.Context, identity domain.ResolvedIdentity, raw string, body any, form bool, audit domain.LoginAuditContext) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	if _, err := messageReadsOwner(identity); err != nil {
		return err
	}
	id, err := messageReadsParseUUID(raw)
	if err != nil {
		return err
	}
	tx, association, err := s.begin(ctx, identity)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	old, err := tx.AutomationWorkflowForUpdate(ctx, id, association)
	if err != nil {
		return err
	}
	patch, invalid, err := dto.ParseAutomationWorkflowPatch(body, form)
	if err != nil {
		return err
	}
	copyID := ""
	if patch.Association != nil {
		exists, err := tx.AssociationExists(ctx, *patch.Association)
		if err != nil {
			return err
		}
		if !exists {
			invalid = true
		} else if *patch.Association != association {
			return domain.ErrMessageReadForbidden
		}
	}
	if patch.ID != nil && *patch.ID != old.ID {
		exists, err := tx.AutomationWorkflowIDExists(ctx, *patch.ID)
		if err != nil {
			return err
		}
		if exists {
			invalid = true
		} else {
			// save() under a key no row has inserts a copy of the workflow with
			// the validated values; the row the request names stays as it was.
			copyID = *patch.ID
		}
	}
	if invalid {
		return dto.ErrAutomationWorkflowInvalid
	}
	if patch.TreeRejected {
		return dto.ErrAutomationWorkflowFormTreeNotPorted
	}
	oldTree, err := dto.ParseProfileTablesJSON(old.Tree)
	if err != nil {
		return err
	}
	row, tree := old, oldTree
	if patch.Name != nil {
		row.Name = *patch.Name
	}
	if patch.Description != nil {
		row.Description = *patch.Description
	}
	if patch.Tree != nil {
		tree = *patch.Tree
	}
	if patch.Enabled != nil {
		row.Enabled = *patch.Enabled
	}
	reset, outcome, scheduleAt := false, automationWorkflowTriggerNone, time.Time{}
	if !old.Enabled && row.Enabled {
		rome, err := time.LoadLocation("Europe/Rome")
		if err != nil {
			return err
		}
		reset, outcome, scheduleAt = automationWorkflowEnablePlan(tree, s.now(), rome)
		if outcome == automationWorkflowTriggerSchedule && s.tasks == nil {
			return domain.ErrAutomationWorkflowUpdateNotPorted
		}
	}
	save := func(before map[string]string) error {
		at := s.now().UTC().Truncate(time.Microsecond)
		if copyID != "" {
			// auditlog's pre_save finds no row under the new key and logs the
			// whole instance as an update (indexed from its changes); the insert
			// sets both timestamps and post_save logs the create.
			row.ID, row.Tree = copyID, json.RawMessage(dto.PythonJSONDumps(tree))
			copyID = ""
			auditContext := paymentCategoryAuditContext(identity, audit)
			changes, err := automationWorkflowChanges(row, 0)
			if err != nil {
				return err
			}
			if err = tx.AppendAutomationWorkflowAudit(ctx, domain.AutomationWorkflowAudit{ID: row.ID, AssociationID: association, Action: 1,
				Changes: changes, Context: auditContext, At: at, Path: "changes_json.sport_association"}); err != nil {
				return err
			}
			row.CreatedAt, row.UpdatedAt = &at, &at
			if err = tx.InsertAutomationWorkflow(ctx, row); err != nil {
				return err
			}
			if changes, err = automationWorkflowChanges(row, 0); err != nil {
				return err
			}
			return tx.AppendAutomationWorkflowAudit(ctx, domain.AutomationWorkflowAudit{ID: row.ID, AssociationID: association, Action: 0,
				Changes: changes, Context: auditContext, At: at})
		}
		row.Tree, row.UpdatedAt = json.RawMessage(dto.PythonJSONDumps(tree)), &at
		if err := tx.UpdateAutomationWorkflow(ctx, row); err != nil {
			return err
		}
		changes := automationWorkflowUpdateChanges(before, automationWorkflowUpdateValues(row, tree))
		if len(changes) == 0 {
			return nil
		}
		return tx.AppendAutomationWorkflowAudit(ctx, domain.AutomationWorkflowAudit{ID: row.ID, AssociationID: association, Action: 1,
			Changes: changes, Context: paymentCategoryAuditContext(identity, audit), At: at})
	}
	if err = save(automationWorkflowUpdateValues(old, oldTree)); err != nil {
		return err
	}
	if reset {
		before := automationWorkflowUpdateValues(row, tree)
		tree = automationWorkflowResetTriggered(tree)
		if err = save(before); err != nil {
			return err
		}
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	if err = tx.Commit(); err != nil {
		return err
	}
	if outcome == automationWorkflowTriggerError {
		return domain.ErrAutomationWorkflowTrigger
	}
	if outcome == automationWorkflowTriggerSchedule {
		// trigger(): time - now_rome as the countdown; Celery's eta is
		// app.now() + countdown.
		now := s.now()
		if !scheduleAt.After(now) {
			return nil
		}
		eta := automationWorkflowETA(now, scheduleAt)
		id, err := uuid.Parse(row.ID)
		if err != nil {
			return err
		}
		if _, err := s.tasks.Publish(ctx, celery.Task{Name: "check_workflows_trigger_cron", Args: []any{id}, ArgsList: true,
			ETA: &eta}); err != nil {
			return err
		}
	}
	return nil
}

// automationWorkflowResetTriggered returns the tree with the first node's
// data['triggered'] set to False (a copy; the plan checked the shape).
func automationWorkflowResetTriggered(tree any) any {
	nodes := append([]any(nil), tree.([]any)...)
	first := append(domain.RequestObject(nil), nodes[0].(domain.RequestObject)...)
	for index, field := range first {
		if field.Name == "data" {
			data := append(domain.RequestObject(nil), field.Value.(domain.RequestObject)...)
			for position := range data {
				if data[position].Name == "triggered" {
					data[position].Value = false
				}
			}
			first[index].Value = data
		}
	}
	nodes[0] = first
	return nodes
}
