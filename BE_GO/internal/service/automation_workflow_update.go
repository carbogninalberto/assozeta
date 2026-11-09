package service

import (
	"context"
	"encoding/json"
	"regexp"
	"strconv"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
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
	automationWorkflowTriggerNotPorted
)

var (
	automationWorkflowCronTime = regexp.MustCompile(`^([0-9]{4})-([0-9]{2})-([0-9]{2})(?:[T ]([0-9]{2}):([0-9]{2})(?::([0-9]{2})(?:\.[0-9]{1,6})?)?(Z|[+-][0-9]{2}:[0-9]{2})?)?$`)
	automationWorkflowYear     = regexp.MustCompile(`^[0-9]{4}`)
)

// automationWorkflowEnablePlan follows the view when a save turns a workflow
// on: whether the try block resets a cron trigger's 'triggered' flag (a second
// save), and what trigger() then does with the tree. trigger() errors are a
// 500 after the saves; a cron time that is not clearly in the past (Celery
// countdown) or not classified here is not ported.
func automationWorkflowEnablePlan(tree any, now time.Time, rome *time.Location) (bool, automationWorkflowTriggerOutcome) {
	nodes, isList := tree.([]any)
	if !isList || len(nodes) == 0 {
		return false, automationWorkflowTriggerError
	}
	first, isObject := nodes[0].(domain.RequestObject)
	if !isObject {
		return false, automationWorkflowTriggerError
	}
	value, present := first.Lookup("value")
	if !present {
		return false, automationWorkflowTriggerError
	}
	data, hasData := first.Lookup("data")
	object, dataObject := data.(domain.RequestObject)
	reset := false
	if value == "cron" && hasData && dataObject {
		if triggered, found := object.Lookup("triggered"); found && triggered == true {
			reset = true
		}
	}
	switch {
	case value == nil:
		if len(nodes) > 1 {
			return reset, automationWorkflowTriggerError
		}
		return reset, automationWorkflowTriggerNone
	case value != "cron":
		return reset, automationWorkflowTriggerNone
	}
	if !dataObject {
		return reset, automationWorkflowTriggerError
	}
	raw, found := object.Lookup("time")
	text, isString := raw.(string)
	if !found || !isString || !automationWorkflowYear.MatchString(text) {
		return reset, automationWorkflowTriggerError
	}
	match := automationWorkflowCronTime.FindStringSubmatch(text)
	if match == nil {
		return reset, automationWorkflowTriggerNotPorted
	}
	number := func(index int) int {
		parsed, _ := strconv.Atoi(match[index])
		return parsed
	}
	year, month, day := number(1), number(2), number(3)
	hour, minute, second := number(4), number(5), number(6)
	if month < 1 || month > 12 || day < 1 || day > time.Date(year, time.Month(month)+1, 0, 0, 0, 0, 0, time.UTC).Day() ||
		year < 1 || hour > 23 || minute > 59 || second > 59 {
		return reset, automationWorkflowTriggerError
	}
	if match[7] != "" {
		// pytz localize() rejects an aware datetime.
		return reset, automationWorkflowTriggerError
	}
	at := time.Date(year, time.Month(month), day, hour, minute, second, 0, rome)
	// Past times return without effect; keep clear of DST and clock differences.
	if at.Before(now.Add(-2 * time.Hour)) {
		return reset, automationWorkflowTriggerNone
	}
	return reset, automationWorkflowTriggerNotPorted
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
		} else if !invalid {
			// save() with a new key inserts a copy: not ported.
			return domain.ErrAutomationWorkflowUpdateNotPorted
		}
	}
	if invalid {
		return dto.ErrAutomationWorkflowInvalid
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
	reset, outcome := false, automationWorkflowTriggerNone
	if !old.Enabled && row.Enabled {
		rome, err := time.LoadLocation("Europe/Rome")
		if err != nil {
			return err
		}
		reset, outcome = automationWorkflowEnablePlan(tree, s.now(), rome)
		if outcome == automationWorkflowTriggerNotPorted {
			return domain.ErrAutomationWorkflowUpdateNotPorted
		}
	}
	save := func(before map[string]string) error {
		at := s.now().UTC().Truncate(time.Microsecond)
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
