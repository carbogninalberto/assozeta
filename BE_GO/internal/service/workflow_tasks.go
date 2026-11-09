package service

import (
	"context"
	"errors"
	"math"
	"math/big"
	"math/rand/v2"
	"regexp"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/celery"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/celeryworker"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/google/uuid"
)

// Celery names of the automation workflow tasks (BE/application/tasks.py).
const (
	CheckWorkflowsTriggerTask     = "check_workflows_trigger"
	CheckWorkflowsTriggerCronTask = "check_workflows_trigger_cron"
	WorkflowBlockExecuteTask      = "workflow_block_execute"
	workflowTasksSendEmailHTML    = "send_email_html"
)

// WorkflowTaskStore is the database side of the workflow tasks.
type WorkflowTaskStore interface {
	AssociationWorkflows(ctx context.Context, association uuid.UUID) ([]domain.WorkflowTaskWorkflow, error)
	EnabledWorkflow(ctx context.Context, id uuid.UUID) (*domain.WorkflowTaskWorkflow, error)
	BlockWorkflow(ctx context.Context, id uuid.UUID) (*domain.WorkflowTaskBlockWorkflow, error)
	SubscriptionExists(ctx context.Context, id uuid.UUID) (bool, error)
	BlockSubscription(ctx context.Context, id uuid.UUID) (*domain.WorkflowTaskSubscription, error)
	Tag(ctx context.Context, id uuid.UUID) (*domain.WorkflowTaskTag, error)
	CronSubscriptions(ctx context.Context, association uuid.UUID, today time.Time, filter domain.WorkflowTaskCronFilter) ([]uuid.UUID, error)
	FirstTutorRelation(ctx context.Context, associate uuid.UUID, primaryOnly bool) (*domain.TutorRelation, error)
	AssociateEmail(ctx context.Context, associate uuid.UUID) (*string, error)
	PromoteTutorRelation(ctx context.Context, relation domain.TutorRelation, at time.Time) error
}

// WorkflowTasks runs check_workflows_trigger, check_workflows_trigger_cron
// and workflow_block_execute. Every body is wrapped in Django's
// try/except Exception: a Python exception ends the task with SUCCESS and
// None after the effects already performed. Plan performs every read and
// decides the effects (Celery messages and the tutor promotion) in Django's
// order; Run performs them. Inputs whose Python semantics are not
// reproduced are handed off before any effect.
type WorkflowTasks struct {
	store     WorkflowTaskStore
	publisher celery.Publisher
	now       func() time.Time
	randint   func(low, high int) int
}

func NewWorkflowTasks(store WorkflowTaskStore, publisher celery.Publisher, now func() time.Time) *WorkflowTasks {
	return &WorkflowTasks{store: store, publisher: publisher, now: now,
		randint: func(low, high int) int { return low + rand.IntN(high-low+1) }}
}

// Tasks returns the three registrations (shared_task defaults: max_retries 3, never used).
func (s *WorkflowTasks) Tasks() map[string]celeryworker.Task {
	return map[string]celeryworker.Task{
		CheckWorkflowsTriggerTask:     {MaxRetries: 3, Plan: s.planTrigger, Run: s.run},
		CheckWorkflowsTriggerCronTask: {MaxRetries: 3, Plan: s.planTriggerCron, Run: s.run},
		WorkflowBlockExecuteTask:      {MaxRetries: 3, Plan: s.planBlock, Run: s.run},
	}
}

// workflowTasksPyError stands for a Python exception inside the task body
// (caught and logged by the task): execution stops, the result is None.
var workflowTasksPyError = errors.New("workflow task: python exception")

// workflowTasksAction is one effect: a Celery send_task or the tutor promotion.
type workflowTasksAction struct {
	task      *celery.Task
	countdown int64
	promote   *domain.TutorRelation
}

type workflowTasksPlan struct {
	actions []workflowTasksAction
	result  any
	err     error
}

func (p *workflowTasksPlan) publish(name string, args []any, kwargs celery.Dict, countdown int64) {
	task := &celery.Task{Name: name, Args: args, Kwargs: kwargs, ArgsList: args != nil}
	p.actions = append(p.actions, workflowTasksAction{task: task, countdown: countdown})
}

// finish stores the plan, mapping a Python exception to the None result.
func (p *workflowTasksPlan) finish(request *celeryworker.Request, err error) error {
	switch {
	case errors.Is(err, celeryworker.ErrHandoff):
		return err
	case errors.Is(err, workflowTasksPyError):
		p.result = nil
	case err != nil:
		p.err = err
	}
	request.State = p
	return nil
}

// run performs the planned effects in order. Messages are published like
// send_task inside a worker task: root_id from the request (or its id),
// parent_id the request's id. They are not added to request.children:
// CoordinatedTask.__call__ runs the body through Task.__call__, which pushes
// a fresh request whose children the stored result never sees, so Django's
// SUCCESS row keeps meta {"children": []}.
func (s *WorkflowTasks) run(ctx context.Context, request *celeryworker.Request) (any, error) {
	plan, _ := request.State.(*workflowTasksPlan)
	if plan == nil {
		return nil, errors.New("workflow task: missing plan")
	}
	if plan.err != nil {
		return nil, plan.err
	}
	root := request.RootID
	if root == "" {
		root = request.ID
	}
	for _, action := range plan.actions {
		if action.promote != nil {
			if err := s.store.PromoteTutorRelation(ctx, *action.promote, s.now().UTC().Truncate(time.Microsecond)); err != nil {
				return nil, err
			}
			continue
		}
		task := *action.task
		task.RootID, task.ParentID = root, request.ID
		if action.countdown != 0 {
			eta := s.now().UTC().Add(time.Duration(action.countdown) * time.Second)
			task.ETA = &eta
		}
		if _, err := s.publisher.Publish(ctx, task); err != nil {
			return nil, err
		}
	}
	return plan.result, nil
}

// --- argument binding and Python value semantics -----------------------------

// workflowTasksBind applies a Python signature whose parameters after the
// first `required` default to None; a binding Python would reject is handed off.
func workflowTasksBind(request *celeryworker.Request, parameters []string, required int) ([]any, error) {
	if len(request.Args) > len(parameters) {
		return nil, celeryworker.ErrHandoff
	}
	values := make([]any, len(parameters))
	present := make([]bool, len(parameters))
	for i, value := range request.Args {
		values[i], present[i] = value, true
	}
	for _, item := range request.Kwargs {
		index := -1
		for i, name := range parameters {
			if name == item.Key {
				index = i
			}
		}
		if index < 0 || present[index] {
			return nil, celeryworker.ErrHandoff
		}
		values[index], present[index] = item.Value, true
	}
	for i := 0; i < required; i++ {
		if !present[i] {
			return nil, celeryworker.ErrHandoff
		}
	}
	return values, nil
}

// workflowTasksUUID is a UUIDField lookup value (UUIDField.to_python): nil
// for None (IS NULL, no row); a str that uuid.UUID(hex=...) rejects raises
// ValidationError (workflowTasksPyError); other types are handed off.
func workflowTasksUUID(value any) (*uuid.UUID, error) {
	switch v := value.(type) {
	case nil:
		return nil, nil
	case uuid.UUID:
		return &v, nil
	case string:
		text, ok := dto.AuditIndexUUIDText(v)
		if !ok {
			return nil, workflowTasksPyError
		}
		id := uuid.MustParse(text)
		return &id, nil
	}
	return nil, celeryworker.ErrHandoff
}

// workflowTasksIndex is Python's container[key] for JSON values: a dict
// (string keys) by str key, a list or str by int index (negative from the
// end); anything else raises.
func workflowTasksIndex(container, key any) (any, error) {
	switch c := container.(type) {
	case celery.Dict:
		name, ok := key.(string)
		if !ok {
			return nil, workflowTasksPyError // KeyError: JSON object keys are str
		}
		value, found := c.Get(name)
		if !found {
			return nil, workflowTasksPyError
		}
		return value, nil
	case []any:
		index, ok := key.(int64)
		if !ok {
			return nil, workflowTasksPyError
		}
		if index < 0 {
			index += int64(len(c))
		}
		if index < 0 || index >= int64(len(c)) {
			return nil, workflowTasksPyError
		}
		return c[index], nil
	case string:
		index, ok := key.(int64)
		if !ok {
			return nil, workflowTasksPyError
		}
		runes := []rune(c)
		if index < 0 {
			index += int64(len(runes))
		}
		if index < 0 || index >= int64(len(runes)) {
			return nil, workflowTasksPyError
		}
		return string(runes[index]), nil
	}
	return nil, workflowTasksPyError
}

// workflowTasksPath is container[k1][k2]...
func workflowTasksPath(container any, keys ...any) (any, error) {
	var err error
	for _, key := range keys {
		if container, err = workflowTasksIndex(container, key); err != nil {
			return nil, err
		}
	}
	return container, nil
}

// workflowTasksLen is len() of a JSON value.
func workflowTasksLen(value any) (int64, error) {
	switch v := value.(type) {
	case celery.Dict:
		return int64(len(v)), nil
	case []any:
		return int64(len(v)), nil
	case string:
		return int64(utf8.RuneCountInString(v)), nil
	}
	return 0, workflowTasksPyError
}

// workflowTasksIs is value == text for a JSON value (only an equal str).
func workflowTasksIs(value any, text string) bool {
	s, ok := value.(string)
	return ok && s == text
}

func workflowTasksDecodeTree(text string) (any, error) {
	tree, err := celery.DecodeJSON([]byte(text))
	if err != nil {
		return nil, celeryworker.ErrHandoff
	}
	return tree, nil
}

// workflowTasksPortable reports a JSON value the producer publishes as
// kombu and saferepr would: floats are handed off (their repr is not
// reproduced).
func workflowTasksPortable(value any) bool {
	switch v := value.(type) {
	case nil, bool, string:
		return true
	case celery.Number:
		return !strings.ContainsAny(string(v), ".eE")
	case []any:
		for _, item := range v {
			if !workflowTasksPortable(item) {
				return false
			}
		}
		return true
	case celery.Dict:
		for _, item := range v {
			if !workflowTasksPortable(item.Value) {
				return false
			}
		}
		return true
	}
	return false
}

var workflowTasksDecimal = regexp.MustCompile(`^[ \t\n\r\f\v]*[+-]?[0-9]+[ \t\n\r\f\v]*$`)

// workflowTasksInt is int(value) for the wait amount: JSON integers and
// plain decimal strings; None, lists and dicts raise TypeError; floats,
// bools and other strings are handed off.
func workflowTasksInt(value any) (int64, error) {
	var text string
	switch v := value.(type) {
	case nil, []any, celery.Dict:
		return 0, workflowTasksPyError
	case celery.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return 0, celeryworker.ErrHandoff
		}
		text = string(v)
	case string:
		if !workflowTasksDecimal.MatchString(v) {
			return 0, celeryworker.ErrHandoff
		}
		text = strings.TrimSpace(v)
	default:
		return 0, celeryworker.ErrHandoff
	}
	number, ok := new(big.Int).SetString(strings.TrimPrefix(text, "+"), 10)
	if !ok || !number.IsInt64() {
		return 0, celeryworker.ErrHandoff
	}
	return number.Int64(), nil
}

// workflowTasksIdx is the block index: an int (bool handed off: True is 1);
// anything else fails before or at idx + 1.
func workflowTasksIdx(value any) (int64, error) {
	switch v := value.(type) {
	case bool:
		return 0, celeryworker.ErrHandoff
	case celery.Number:
		if strings.ContainsAny(string(v), ".eE") {
			return 0, workflowTasksPyError
		}
		number, ok := new(big.Int).SetString(string(v), 10)
		if !ok || !number.IsInt64() || number.Int64() == math.MaxInt64 {
			return 0, celeryworker.ErrHandoff
		}
		return number.Int64(), nil
	}
	return 0, workflowTasksPyError
}

// --- check_workflows_trigger -------------------------------------------------

func (s *WorkflowTasks) planTrigger(ctx context.Context, request *celeryworker.Request) error {
	plan := &workflowTasksPlan{}
	return plan.finish(request, s.trigger(ctx, request, plan))
}

func (s *WorkflowTasks) trigger(ctx context.Context, request *celeryworker.Request, plan *workflowTasksPlan) error {
	values, err := workflowTasksBind(request, []string{"trigger_type", "sport_association_id", "subscription_id", "data"}, 2)
	if err != nil {
		return err
	}
	triggerType := values[0]
	if _, ok := triggerType.(string); !ok && triggerType != nil {
		return celeryworker.ErrHandoff
	}
	if _, err = workflowTasksUUID(values[2]); errors.Is(err, celeryworker.ErrHandoff) {
		return err
	}
	association, err := workflowTasksUUID(values[1])
	if err != nil {
		return err
	}
	if association == nil {
		return nil // filter(sport_association=None): no workflows
	}
	workflows, err := s.store.AssociationWorkflows(ctx, *association)
	if err != nil || len(workflows) == 0 {
		return err
	}
	for _, workflow := range workflows {
		tree, err := workflowTasksDecodeTree(workflow.Tree)
		if err != nil {
			return err
		}
		node, err := workflowTasksPath(tree, int64(0), "value")
		if err != nil {
			return err
		}
		matches := triggerType == nil && node == nil
		if text, ok := triggerType.(string); ok {
			matches = workflowTasksIs(node, text)
		}
		if !matches {
			continue
		}
		subscription, err := workflowTasksUUID(values[2])
		if err != nil {
			return err
		}
		exists := false
		if subscription != nil {
			if exists, err = s.store.SubscriptionExists(ctx, *subscription); err != nil {
				return err
			}
		}
		if !exists {
			return nil
		}
		if err = s.fire(ctx, plan, workflow, tree, node, *subscription); err != nil {
			return err
		}
	}
	return nil
}

// fire is AutomationWorkflow.trigger(trigger_type, subscription) once the
// trigger type equals the first node's value.
func (s *WorkflowTasks) fire(ctx context.Context, plan *workflowTasksPlan, workflow domain.WorkflowTaskWorkflow, tree, node any,
	subscription uuid.UUID) error {
	if workflowTasksIs(node, "cron") {
		return s.cron(ctx, plan, workflow, tree)
	}
	if size, err := workflowTasksLen(tree); err != nil || size <= 1 {
		return err
	}
	plan.publish(WorkflowBlockExecuteTask, []any{workflow.ID, subscription, int64(1)}, nil, 0)
	return nil
}

// workflowTasksStatuses are the cron targets filtered by status_flag with archived=False.
var workflowTasksStatuses = map[string]int{"approved": 4, "rejected": 3, "not_signed": 1, "pending": 2}

// cron is trigger()'s cron branch: one workflow_block_execute(workflow,
// subscription, 1) per subscription of the target active today (UTC).
func (s *WorkflowTasks) cron(ctx context.Context, plan *workflowTasksPlan, workflow domain.WorkflowTaskWorkflow, tree any) error {
	target, err := workflowTasksPath(tree, int64(0), "data", "target")
	if err != nil {
		return err
	}
	if _, ok := target.(string); !ok && target != nil {
		return celeryworker.ErrHandoff
	}
	today := s.now().UTC()
	today = time.Date(today.Year(), today.Month(), today.Day(), 0, 0, 0, 0, time.UTC)
	filter := domain.WorkflowTaskCronFilter{}
	text, _ := target.(string)
	switch status, known := workflowTasksStatuses[text]; {
	case target != nil && text == "all":
	case target != nil && known:
		filter.Status = &status
	case target != nil && text == "archived":
		archived := true
		filter.Archived = &archived
	default:
		id, err := workflowTasksUUID(target)
		if err != nil {
			return err
		}
		if id != nil {
			if filter.Tag, err = s.store.Tag(ctx, *id); err != nil {
				return err
			}
		}
	}
	if size, err := workflowTasksLen(tree); err != nil || size <= 1 {
		return err
	}
	subscriptions, err := s.store.CronSubscriptions(ctx, workflow.Association, today, filter)
	if err != nil {
		return err
	}
	for _, subscription := range subscriptions {
		plan.publish(WorkflowBlockExecuteTask, []any{workflow.ID, subscription, int64(1)}, nil, 0)
	}
	return nil
}

// --- check_workflows_trigger_cron --------------------------------------------

func (s *WorkflowTasks) planTriggerCron(ctx context.Context, request *celeryworker.Request) error {
	plan := &workflowTasksPlan{}
	return plan.finish(request, s.triggerCron(ctx, request, plan))
}

func (s *WorkflowTasks) triggerCron(ctx context.Context, request *celeryworker.Request, plan *workflowTasksPlan) error {
	values, err := workflowTasksBind(request, []string{"workflow_automation_id"}, 1)
	if err != nil {
		return err
	}
	id, err := workflowTasksUUID(values[0])
	if err != nil || id == nil {
		return err
	}
	workflow, err := s.store.EnabledWorkflow(ctx, *id)
	if err != nil || workflow == nil {
		return err
	}
	tree, err := workflowTasksDecodeTree(workflow.Tree)
	if err != nil {
		return err
	}
	size, err := workflowTasksLen(tree)
	if err != nil || size <= 1 {
		return err
	}
	node, err := workflowTasksPath(tree, int64(0), "value")
	if err != nil {
		return err
	}
	if !workflowTasksIs(node, "cron") {
		return nil // trigger('cron') with another first node does nothing
	}
	return s.cron(ctx, plan, *workflow, tree)
}

// --- workflow_block_execute --------------------------------------------------

func (s *WorkflowTasks) planBlock(ctx context.Context, request *celeryworker.Request) error {
	plan := &workflowTasksPlan{}
	return plan.finish(request, s.block(ctx, request, plan))
}

func (s *WorkflowTasks) block(ctx context.Context, request *celeryworker.Request, plan *workflowTasksPlan) error {
	values, err := workflowTasksBind(request, []string{"workflow_id", "subscription_id", "idx"}, 1)
	if err != nil {
		return err
	}
	if _, err = workflowTasksIdx(values[2]); errors.Is(err, celeryworker.ErrHandoff) {
		return err
	}
	if _, err = workflowTasksUUID(values[0]); errors.Is(err, celeryworker.ErrHandoff) {
		return err
	}
	subscriptionID, err := workflowTasksUUID(values[1])
	if err != nil {
		return err
	}
	var subscription *domain.WorkflowTaskSubscription
	if subscriptionID != nil {
		if subscription, err = s.store.BlockSubscription(ctx, *subscriptionID); err != nil {
			return err
		}
	}
	workflowID, err := workflowTasksUUID(values[0])
	if err != nil {
		return err
	}
	var workflow *domain.WorkflowTaskBlockWorkflow
	if workflowID != nil {
		if workflow, err = s.store.BlockWorkflow(ctx, *workflowID); err != nil {
			return err
		}
	}
	if workflow == nil {
		return nil
	}
	if subscription == nil {
		plan.result = []any{false, "Subscription is None"}
		return nil
	}
	tree, err := workflowTasksDecodeTree(workflow.Tree)
	if err != nil {
		return err
	}
	idx, err := workflowTasksIdx(values[2])
	if err != nil {
		return err
	}
	if _, ok := tree.([]any); !ok {
		return workflowTasksPyError // a dict has no int keys; a str node has no ['id']
	}
	node, err := workflowTasksIndex(tree, idx)
	if err != nil {
		return err
	}
	size, _ := workflowTasksLen(tree)
	var next any
	if idx+1 < size {
		next = idx + 1
	}
	id, err := workflowTasksIndex(node, "id")
	if err != nil {
		return err
	}
	switch {
	case workflowTasksIs(id, "message"):
		value, err := workflowTasksIndex(node, "value")
		if err != nil || !workflowTasksIs(value, "email") {
			return err
		}
		return s.email(ctx, plan, values[0], workflow, subscription, node, next)
	case workflowTasksIs(id, "wait"):
		return s.wait(plan, values[0], subscription, node, next)
	}
	return nil
}

// email is the message/email block: send_email_html to the computed
// recipients and the next block, both with the same random countdown.
func (s *WorkflowTasks) email(ctx context.Context, plan *workflowTasksPlan, workflowArg any, workflow *domain.WorkflowTaskBlockWorkflow,
	subscription *domain.WorkflowTaskSubscription, node, next any) error {
	content, err := workflowTasksPath(node, "data", "content")
	if err != nil {
		return err
	}
	subject, err := workflowTasksPath(node, "data", "subject")
	if err != nil {
		return err
	}
	recipients, err := workflowTasksPath(node, "data", "recipients")
	if err != nil {
		return err
	}
	if !workflowTasksPortable(content) || !workflowTasksPortable(subject) {
		return celeryworker.ErrHandoff
	}
	athletes := workflowTasksIs(recipients, "athletes")
	var list []string
	if athletes || workflowTasksIs(recipients, "all") {
		sameUser := workflow.OwnerID == subscription.UserID
		if !(sameUser && athletes) {
			list = append(list, subscription.UserEmail)
			if !sameUser {
				list = append(list, workflow.OwnerEmail)
			}
		}
		if subscription.AssociateID == nil {
			return workflowTasksPyError // None.email
		}
		if subscription.AssociateEmail != nil && *subscription.AssociateEmail != "" {
			list = append(list, *subscription.AssociateEmail)
		}
		tutorEmail, err := s.mainTutor(ctx, plan, *subscription.AssociateID)
		if err != nil {
			return err
		}
		if subscription.AssociateMinor && tutorEmail != nil && *tutorEmail != "" {
			list = append(list, *tutorEmail)
		}
	} else {
		list = []string{workflow.OwnerEmail}
	}
	// list(set(recipient_list)): Python's order follows the per-process
	// string hash seed; Go keeps the first occurrence of each address.
	recipientList := []any{}
	seen := map[string]bool{}
	for _, email := range list {
		if !seen[email] {
			seen[email] = true
			recipientList = append(recipientList, email)
		}
	}
	countdown := int64(s.randint(1, 180))
	plan.publish(workflowTasksSendEmailHTML, nil, celery.Dict{{Key: "html_message", Value: content}, {Key: "subject", Value: subject},
		{Key: "recipient_list", Value: recipientList}, {Key: "sport_association_id", Value: workflow.Association},
		{Key: "reply_to", Value: []any{workflow.OwnerEmail}}}, countdown)
	if next != nil {
		plan.publish(WorkflowBlockExecuteTask, []any{workflowArg, subscription.ID, next}, nil, countdown)
	}
	return nil
}

// mainTutor is Associate.get_main_tutor(): the primary relation, else the
// first one promoted to primary (saved, audited); the tutor's email.
func (s *WorkflowTasks) mainTutor(ctx context.Context, plan *workflowTasksPlan, associate uuid.UUID) (*string, error) {
	relation, err := s.store.FirstTutorRelation(ctx, associate, true)
	if err != nil {
		return nil, err
	}
	if relation == nil {
		if relation, err = s.store.FirstTutorRelation(ctx, associate, false); err != nil || relation == nil {
			return nil, err
		}
		promoted := *relation
		promoted.Primary = true
		plan.actions = append(plan.actions, workflowTasksAction{promote: &promoted})
	}
	email, err := s.store.AssociateEmail(ctx, relation.Tutor)
	if err != nil {
		return nil, err
	}
	if email == nil {
		empty := ""
		return &empty, nil
	}
	return email, nil
}

// workflowTasksUnits are the wait block's countdown factors.
var workflowTasksUnits = map[string]int64{"minutes": 60, "hours": 3600, "days": 86400}

// wait schedules the next block after amount minutes, hours or days.
func (s *WorkflowTasks) wait(plan *workflowTasksPlan, workflowArg any, subscription *domain.WorkflowTaskSubscription, node, next any) error {
	unit, err := workflowTasksPath(node, "data", "type")
	if err != nil {
		return err
	}
	text, _ := unit.(string)
	factor, known := workflowTasksUnits[text]
	known = known && unit != nil
	var countdown int64
	if known {
		amount, err := workflowTasksPath(node, "data", "amount")
		if err != nil {
			return err
		}
		value, err := workflowTasksInt(amount)
		if err != nil {
			return err
		}
		// Celery's countdown bounds (INT_MIN) and timedelta's range.
		if value < math.MinInt32/factor || value > math.MaxInt32/factor {
			return celeryworker.ErrHandoff
		}
		countdown = value * factor
	}
	if next == nil {
		return nil
	}
	if !known {
		return workflowTasksPyError // countdown is unbound
	}
	plan.publish(WorkflowBlockExecuteTask, []any{workflowArg, subscription.ID, next}, nil, countdown)
	return nil
}
