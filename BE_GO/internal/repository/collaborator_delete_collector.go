package repository

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/dto"
	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
	"github.com/google/uuid"
	"github.com/jmoiron/sqlx"
)

// collaborator_delete_graph.json is Django's deletion graph for a User hard
// delete (TESTS/adapters/django/collaborator_delete_go_graph.py writes it from
// the models' _meta; the HTTP family fails when it drifts): every model the
// Collector reaches from application.User through CASCADE, plus Payment and
// CourseSubscriptionInstallment (deleted by the ported pre_delete receivers),
// with candidate relations in _meta.get_fields(include_hidden=True) order,
// Django's own can_fast_delete verdicts, signal receivers and auditlog's
// delete-diff fields.
//
//go:embed collaborator_delete_graph.json
var collaboratorDeleteGraphJSON []byte

type collaboratorDeleteRelation struct {
	Model        string `json:"model"`
	Table        string `json:"table"`
	Field        string `json:"field"`
	Column       string `json:"column"`
	TargetColumn string `json:"target_column"`
	OnDelete     string `json:"on_delete"`
	Null         bool   `json:"null"`
	Fast         bool   `json:"fast"`
}

type collaboratorDeleteAuditField struct {
	Name    string `json:"name"`
	Kind    string `json:"kind"`
	Column  string `json:"column"`
	Type    string `json:"type"`
	Text    string `json:"text"`
	Related string `json:"related"`
}

type collaboratorDeleteAudit struct {
	Include       []string                       `json:"include"`
	Exclude       []string                       `json:"exclude"`
	Mask          []string                       `json:"mask"`
	SerializeData bool                           `json:"serialize_data"`
	Fields        []collaboratorDeleteAuditField `json:"fields"`
}

type collaboratorDeleteModel struct {
	Label          string
	AppLabel       string                       `json:"app_label"`
	ModelName      string                       `json:"model_name"`
	ObjectName     string                       `json:"object_name"`
	Table          string                       `json:"table"`
	PK             string                       `json:"pk"`
	PKType         string                       `json:"pk_type"`
	AutoCreated    bool                         `json:"auto_created"`
	PreDelete      []string                     `json:"pre_delete"`
	PostDelete     []string                     `json:"post_delete"`
	FastRoot       bool                         `json:"fast_root"`
	Relations      []collaboratorDeleteRelation `json:"relations"`
	Audit          *collaboratorDeleteAudit     `json:"audit"`
	Str            *struct{ Class string }      `json:"str"`
	AdditionalData *string                      `json:"additional_data"`
	// unsupported names why rows of this model cannot be deleted here
	// (refused before any write of the collector that reaches them).
	unsupported string
}

type collaboratorDeleteGraph struct {
	Order  []string                            `json:"order"`
	Models map[string]*collaboratorDeleteModel `json:"models"`
}

const (
	collaboratorDeletePayment            = "application.Payment"
	collaboratorDeleteCourseSubscription = "application.CourseSubscription"
	collaboratorDeleteInstallment        = "application.CourseSubscriptionInstallment"
	collaboratorDeleteUser               = "application.User"
	collaboratorDeleteInvite             = "application.CollaborationInvites"
)

// ErrCollaboratorDeleteUnsupported refuses a deletion that reaches rows whose
// Django behaviour this port does not reproduce (see collaborator-delete-go.md).
var ErrCollaboratorDeleteUnsupported = refusal.New("collaborator delete reaches an unsupported model")

// ErrCollaboratorDeleteProtected is Django's ProtectedError (a 500).
var ErrCollaboratorDeleteProtected = errors.New("cannot delete: referenced through a protected foreign key")

var collaboratorDeleteValueTypes = map[string]bool{
	"CharField": true, "TextField": true, "UUIDField": true, "BooleanField": true, "IntegerField": true,
	"PositiveIntegerField": true, "PositiveSmallIntegerField": true, "SmallIntegerField": true, "BigIntegerField": true,
	"AutoField": true, "BigAutoField": true, "DecimalField": true, "DateField": true, "TimeField": true,
}

var collaboratorDeleteReceivers = map[string]string{
	collaboratorDeletePayment:            "application.signals.delete_installment_of_payment",
	collaboratorDeleteCourseSubscription: "application.signals.delete_related_course_payment",
	collaboratorDeleteInstallment:        "application.signals.delete_related_payment",
}

var collaboratorDeleteGraphLoaded = loadCollaboratorDeleteGraph(collaboratorDeleteGraphJSON)

// loadCollaboratorDeleteGraph validates the table once: anything the Go
// collector does not implement marks its model unsupported.
func loadCollaboratorDeleteGraph(raw []byte) *collaboratorDeleteGraph {
	graph := &collaboratorDeleteGraph{}
	if err := json.Unmarshal(raw, graph); err != nil {
		panic("collaborator delete graph: " + err.Error())
	}
	for _, label := range graph.Order {
		model := graph.Models[label]
		if model == nil {
			panic("collaborator delete graph: missing " + label)
		}
		model.Label = label
	}
	for _, label := range graph.Order {
		model := graph.Models[label]
		mark := func(reason string) {
			if model.unsupported == "" {
				model.unsupported = reason
			}
		}
		pre := collaboratorDeleteReceivers[label]
		if len(model.PreDelete) > 1 || (len(model.PreDelete) == 1 && model.PreDelete[0] != pre) {
			mark("pre_delete receiver")
		}
		if len(model.PostDelete) > 0 && (len(model.PostDelete) > 1 || model.PostDelete[0] != "auditlog.receivers.log_delete" || model.Audit == nil) {
			mark("post_delete receiver")
		}
		if !collaboratorDeleteKeyType(model.PKType) {
			mark("primary key type")
		}
		for _, relation := range model.Relations {
			if relation.TargetColumn != model.PK {
				mark("relation to a non-key column")
			}
			switch relation.OnDelete {
			case "CASCADE":
				if graph.Models[relation.Model] == nil {
					mark("cascade outside the graph")
				}
			case "SET_NULL", "PROTECT", "DO_NOTHING":
			default:
				mark("on_delete " + relation.OnDelete)
			}
		}
		if model.Audit != nil {
			if len(model.Audit.Mask) > 0 || model.Audit.SerializeData || model.AdditionalData != nil {
				mark("audit options")
			}
			if model.Str != nil && label != collaboratorDeleteUser && label != collaboratorDeleteCourseSubscription {
				mark("object representation")
			}
			for _, field := range model.Audit.Fields {
				switch field.Kind {
				case "fk", "datetime", "json", "manager":
				case "reverse_one":
					if related := graph.Models[field.Related]; related == nil || related.Str != nil {
						mark("reverse one-to-one representation")
					}
				case "value":
					if !collaboratorDeleteValueTypes[field.Type] {
						mark("audit field type " + field.Type)
					}
				default:
					mark("audit field " + field.Kind)
				}
			}
		}
	}
	return graph
}

func collaboratorDeleteKeyType(kind string) bool {
	return kind == "UUIDField" || kind == "AutoField" || kind == "BigAutoField"
}

func collaboratorDeleteArray(kind string) string {
	if kind == "UUIDField" {
		return "::text[]::uuid[]"
	}
	return "::text[]::bigint[]"
}

// collaboratorDeleteRun is one request: its transaction, audit context, the
// X-Group-Id the group-aware managers filter by, and whether
// delete_installment_of_payment is disconnected (it disconnects itself
// around its installments' delete()).
type collaboratorDeleteRun struct {
	tx           *sqlx.Tx
	graph        *collaboratorDeleteGraph
	audit        domain.LoginAuditContext
	group        *string
	disconnected bool
	now          func() time.Time
}

type collaboratorDeleteRow struct {
	key    string
	raw    []byte
	stored map[string]any
	course *domain.CourseSubscriptionRow
}

type collaboratorDeleteBucket struct {
	model *collaboratorDeleteModel
	rows  []*collaboratorDeleteRow
	keys  map[string]bool
}

type collaboratorDeleteFast struct {
	model   *collaboratorDeleteModel
	columns []string
	keys    []string
	keyType string
}

type collaboratorDeleteUpdate struct {
	table, column, keyType string
	keys                   []string
}

// collaboratorDeleteCollector is django.db.models.deletion.Collector.
type collaboratorDeleteCollector struct {
	run          *collaboratorDeleteRun
	order        []string
	data         map[string]*collaboratorDeleteBucket
	dependencies map[string]map[string]bool
	fast         []collaboratorDeleteFast
	updates      []*collaboratorDeleteUpdate
	updateIndex  map[string]*collaboratorDeleteUpdate
}

func (r *collaboratorDeleteRun) collector() *collaboratorDeleteCollector {
	return &collaboratorDeleteCollector{run: r, data: map[string]*collaboratorDeleteBucket{}, dependencies: map[string]map[string]bool{},
		updateIndex: map[string]*collaboratorDeleteUpdate{}}
}

// load reads full rows (the Collector's instances) of model where column is
// one of keys, through the base manager.
func (r *collaboratorDeleteRun) load(ctx context.Context, model *collaboratorDeleteModel, where string, args ...any) ([]*collaboratorDeleteRow, error) {
	type loaded struct {
		Key string `db:"key"`
		Row string `db:"row"`
	}
	found := []loaded{}
	if err := r.tx.SelectContext(ctx, &found, `SELECT t.`+model.PK+`::text AS key, to_jsonb(t)::text AS row FROM `+model.Table+
		` t WHERE `+where, args...); err != nil {
		return nil, err
	}
	rows := make([]*collaboratorDeleteRow, 0, len(found))
	for _, item := range found {
		decoder := json.NewDecoder(strings.NewReader(item.Row))
		decoder.UseNumber()
		stored := map[string]any{}
		if err := decoder.Decode(&stored); err != nil {
			return nil, err
		}
		row := &collaboratorDeleteRow{key: item.Key, raw: []byte(item.Row), stored: stored}
		if model.Label == collaboratorDeleteCourseSubscription {
			course := domain.CourseSubscriptionRow{}
			if err := r.tx.GetContext(ctx, &course, `SELECT `+courseSubscriptionColumns+` FROM application_coursesubscription cs
 JOIN application_course c ON c.course_id=cs.course_id WHERE cs.course_subscription_id=$1`, item.Key); err != nil {
				return nil, err
			}
			row.course = &course
		}
		rows = append(rows, row)
	}
	return rows, nil
}

func (r *collaboratorDeleteRun) loadBy(ctx context.Context, model *collaboratorDeleteModel, column, keyType string, keys []string) ([]*collaboratorDeleteRow, error) {
	return r.load(ctx, model, `t.`+column+` = ANY($1`+collaboratorDeleteArray(keyType)+`)`, keys)
}

// Delete is Model.delete() / QuerySet.delete(): collect then delete.
func (r *collaboratorDeleteRun) Delete(ctx context.Context, label string, rows []*collaboratorDeleteRow) error {
	if len(rows) == 0 {
		return nil
	}
	collector := r.collector()
	if err := collector.collect(ctx, r.graph.Models[label], rows, "", false); err != nil {
		return err
	}
	return collector.delete(ctx)
}

func (c *collaboratorDeleteCollector) bucket(model *collaboratorDeleteModel) *collaboratorDeleteBucket {
	bucket := c.data[model.Label]
	if bucket == nil {
		bucket = &collaboratorDeleteBucket{model: model, keys: map[string]bool{}}
		c.data[model.Label] = bucket
		c.order = append(c.order, model.Label)
	}
	return bucket
}

// collect is Collector.collect(objs, source, nullable): add the new rows,
// then each candidate relation in order (DO_NOTHING skipped, Django's fast
// deletes queued, SET_NULL queued lazily, CASCADE collected recursively,
// PROTECT raising after the loop).
func (c *collaboratorDeleteCollector) collect(ctx context.Context, model *collaboratorDeleteModel, rows []*collaboratorDeleteRow, source string,
	nullable bool) error {
	if model.unsupported != "" {
		return fmt.Errorf("%w: %s (%s)", ErrCollaboratorDeleteUnsupported, model.Label, model.unsupported)
	}
	bucket := c.bucket(model)
	added := []*collaboratorDeleteRow{}
	for _, row := range rows {
		if !bucket.keys[row.key] {
			bucket.keys[row.key] = true
			bucket.rows = append(bucket.rows, row)
			added = append(added, row)
		}
	}
	if source != "" && !nullable {
		if c.dependencies[source] == nil {
			c.dependencies[source] = map[string]bool{}
		}
		c.dependencies[source][model.Label] = true
	}
	if len(added) == 0 {
		return nil
	}
	keys := make([]string, len(added))
	for index, row := range added {
		keys[index] = row.key
	}
	fastOrder, fastColumns := []string{}, map[string][]string{}
	var protected error
	for _, relation := range model.Relations {
		if relation.OnDelete == "DO_NOTHING" {
			continue
		}
		if relation.Fast {
			if _, ok := fastColumns[relation.Model]; !ok {
				fastOrder = append(fastOrder, relation.Model)
			}
			fastColumns[relation.Model] = append(fastColumns[relation.Model], relation.Column)
			continue
		}
		switch relation.OnDelete {
		case "SET_NULL":
			id := relation.Table + "." + relation.Column
			update := c.updateIndex[id]
			if update == nil {
				update = &collaboratorDeleteUpdate{table: relation.Table, column: relation.Column, keyType: model.PKType}
				c.updateIndex[id] = update
				c.updates = append(c.updates, update)
			}
			update.keys = append(update.keys, keys...)
		case "PROTECT":
			var exists bool
			if err := c.run.tx.GetContext(ctx, &exists, `SELECT EXISTS(SELECT 1 FROM `+relation.Table+` WHERE `+relation.Column+` = ANY($1`+
				collaboratorDeleteArray(model.PKType)+`))`, keys); err != nil {
				return err
			}
			if exists {
				protected = ErrCollaboratorDeleteProtected
			}
		case "CASCADE":
			related := c.run.graph.Models[relation.Model]
			sub, err := c.run.loadBy(ctx, related, relation.Column, model.PKType, keys)
			if err != nil {
				return err
			}
			if len(sub) > 0 {
				// ProtectedError from deeper levels is collected and re-raised
				// after this loop; nothing is written before it.
				if err := c.collect(ctx, related, sub, model.Label, relation.Null); err != nil {
					if !errors.Is(err, ErrCollaboratorDeleteProtected) {
						return err
					}
					protected = err
				}
			}
		}
	}
	if protected != nil {
		return protected
	}
	for _, label := range fastOrder {
		related := c.run.graph.Models[label]
		if related == nil {
			return fmt.Errorf("%w: fast delete of %s", ErrCollaboratorDeleteUnsupported, label)
		}
		c.fast = append(c.fast, collaboratorDeleteFast{model: related, columns: fastColumns[label], keys: keys, keyType: model.PKType})
	}
	return nil
}

func collaboratorDeleteLess(kind, left, right string) bool {
	if kind == "UUIDField" {
		return left < right
	}
	a, _ := new(big.Int).SetString(left, 10)
	b, _ := new(big.Int).SetString(right, 10)
	return a.Cmp(b) < 0
}

// sort is Collector.sort(): repeated passes in insertion order, taking each
// model whose dependencies are already placed; a cycle keeps insertion order.
func (c *collaboratorDeleteCollector) sort() {
	sorted, placed := []string{}, map[string]bool{}
	for len(sorted) < len(c.order) {
		found := false
		for _, label := range c.order {
			if placed[label] {
				continue
			}
			ready := true
			for dependency := range c.dependencies[label] {
				if !placed[dependency] {
					ready = false
					break
				}
			}
			if ready {
				sorted = append(sorted, label)
				placed[label] = true
				found = true
			}
		}
		if !found {
			return
		}
	}
	c.order = sorted
}

// delete is Collector.delete(): rows by ascending key, models sorted;
// pre_delete per row; fast deletes; SET_NULL updates; then per model the
// DELETE and post_delete (auditlog) per row in descending key order.
func (c *collaboratorDeleteCollector) delete(ctx context.Context) error {
	for _, bucket := range c.data {
		kind := bucket.model.PKType
		sort.SliceStable(bucket.rows, func(i, j int) bool { return collaboratorDeleteLess(kind, bucket.rows[i].key, bucket.rows[j].key) })
	}
	c.sort()
	if len(c.order) == 1 {
		bucket := c.data[c.order[0]]
		if len(bucket.rows) == 1 && bucket.model.FastRoot {
			_, err := c.run.tx.ExecContext(ctx, `DELETE FROM `+bucket.model.Table+` WHERE `+bucket.model.PK+` = ANY($1`+
				collaboratorDeleteArray(bucket.model.PKType)+`)`, []string{bucket.rows[0].key})
			return err
		}
	}
	for _, label := range c.order {
		bucket := c.data[label]
		if bucket.model.AutoCreated {
			continue
		}
		for _, row := range bucket.rows {
			if err := c.run.preDelete(ctx, bucket.model, row); err != nil {
				return err
			}
		}
	}
	for _, fast := range c.fast {
		conditions := make([]string, len(fast.columns))
		for index, column := range fast.columns {
			conditions[index] = column + ` = ANY($1` + collaboratorDeleteArray(fast.keyType) + `)`
		}
		if _, err := c.run.tx.ExecContext(ctx, `DELETE FROM `+fast.model.Table+` WHERE `+strings.Join(conditions, " OR "), fast.keys); err != nil {
			return err
		}
	}
	for _, update := range c.updates {
		if _, err := c.run.tx.ExecContext(ctx, `UPDATE `+update.table+` SET `+update.column+` = NULL WHERE `+update.column+` = ANY($1`+
			collaboratorDeleteArray(update.keyType)+`)`, update.keys); err != nil {
			return err
		}
	}
	for _, label := range c.order {
		bucket := c.data[label]
		keys := make([]string, len(bucket.rows))
		for index, row := range bucket.rows {
			keys[index] = row.key
		}
		if _, err := c.run.tx.ExecContext(ctx, `DELETE FROM `+bucket.model.Table+` WHERE `+bucket.model.PK+` = ANY($1`+
			collaboratorDeleteArray(bucket.model.PKType)+`)`, keys); err != nil {
			return err
		}
		if bucket.model.AutoCreated || len(bucket.model.PostDelete) == 0 {
			continue
		}
		for index := len(bucket.rows) - 1; index >= 0; index-- {
			if err := c.run.logDelete(ctx, bucket.model, bucket.rows[index]); err != nil {
				return err
			}
		}
	}
	return nil
}

func collaboratorDeleteText(stored map[string]any, column string) *string {
	switch value := stored[column].(type) {
	case string:
		return &value
	case json.Number:
		text := value.String()
		return &text
	}
	return nil
}

func (r *collaboratorDeleteRun) groupFilter(alias string, position int) string {
	return ` AND ($` + strconv.Itoa(position) + `::uuid IS NULL OR ` + alias + `.group_id = $` + strconv.Itoa(position) + `::uuid)`
}

// preDelete runs the model's pre_delete receivers (application/signals.py).
func (r *collaboratorDeleteRun) preDelete(ctx context.Context, model *collaboratorDeleteModel, row *collaboratorDeleteRow) error {
	switch model.Label {
	case collaboratorDeletePayment:
		// delete_installment_of_payment: CourseSubscriptionInstallment.objects
		// (live, X-Group-Id) .filter(payment=instance).delete() with itself
		// disconnected.
		if r.disconnected {
			return nil
		}
		installments := r.graph.Models[collaboratorDeleteInstallment]
		rows, err := r.load(ctx, installments, `t.payment_id = $1::uuid AND NOT t.deleted`+r.groupFilter("t", 2), row.key, r.group)
		if err != nil {
			return err
		}
		r.disconnected = true
		defer func() { r.disconnected = false }()
		return r.Delete(ctx, collaboratorDeleteInstallment, rows)
	case collaboratorDeleteInstallment:
		// delete_related_payment: instance.payment (base manager, read now);
		// an unpaid payment is deleted; a missing one is logged.
		payment := collaboratorDeleteText(row.stored, "payment_id")
		if payment == nil {
			return nil
		}
		rows, err := r.load(ctx, r.graph.Models[collaboratorDeletePayment], `t.payment_id = $1::uuid`, *payment)
		if err != nil || len(rows) == 0 || rows[0].stored["paid"] == true {
			return err
		}
		return r.Delete(ctx, collaboratorDeletePayment, rows)
	case collaboratorDeleteCourseSubscription:
		// delete_related_course_payment: with a payment, Payment.objects.get(id=...)
		// raises FieldError, which the receiver logs; otherwise the unpaid live
		// membership payments (membership type) and the live installments.
		if collaboratorDeleteText(row.stored, "payment_id") != nil {
			return nil
		}
		if kind, _ := row.stored["type"].(json.Number); kind.String() == "3" {
			payments, err := r.load(ctx, r.graph.Models[collaboratorDeletePayment], `t.payment_id IN (SELECT payment_id FROM
 application_coursesubscription_membership_payments WHERE coursesubscription_id = $1::uuid) AND NOT t.paid AND NOT t.deleted`+
				r.groupFilter("t", 2), row.key, r.group)
			if err != nil {
				return err
			}
			if err = r.Delete(ctx, collaboratorDeletePayment, payments); err != nil {
				return err
			}
		}
		rows, err := r.load(ctx, r.graph.Models[collaboratorDeleteInstallment], `t.course_subscription_id = $1::uuid AND NOT t.deleted`+
			r.groupFilter("t", 2), row.key, r.group)
		if err != nil {
			return err
		}
		return r.Delete(ctx, collaboratorDeleteInstallment, rows)
	}
	return nil
}

// logDelete is auditlog's log_delete with the AuditLogIndex post_save
// receiver: every tracked field against None, smart_str(instance), and an
// index row through changes['sport_association'] when that live association
// exists (the deleted row itself no longer resolves), else the actor's.
func (r *collaboratorDeleteRun) logDelete(ctx context.Context, model *collaboratorDeleteModel, row *collaboratorDeleteRow) error {
	changes := map[string][2]string{}
	for _, field := range model.Audit.Fields {
		switch field.Kind {
		case "manager":
			changes[field.Name] = [2]string{field.Text, "None"}
			continue
		case "reverse_one":
			related := r.graph.Models[field.Related]
			var key *string
			if err := r.tx.GetContext(ctx, &key, `SELECT (SELECT `+related.PK+`::text FROM `+related.Table+` WHERE `+field.Column+
				` = ANY($1`+collaboratorDeleteArray(model.PKType)+`) LIMIT 1)`, []string{row.key}); err != nil {
				return err
			}
			if key != nil {
				changes[field.Name] = [2]string{related.ObjectName + " object (" + *key + ")", "None"}
			}
			continue
		}
		value := row.stored[field.Column]
		if value == nil {
			switch field.Kind {
			case "fk", "datetime":
				changes[field.Name] = [2]string{"None", "None"}
			case "json":
				changes[field.Name] = [2]string{"null", "None"}
			}
			continue
		}
		var rendered string
		switch {
		case field.Kind == "json":
			text, err := renderAuditValue("jsonb", value, row.raw, field.Column)
			if err != nil {
				return err
			}
			rendered = text
		case field.Kind == "datetime":
			text, err := renderAuditValue("timestamp", value, row.raw, field.Column)
			if err != nil {
				return err
			}
			rendered = text
		case field.Type == "BooleanField":
			rendered = map[bool]string{true: "True", false: "False"}[value == true]
		case field.Type == "TimeField":
			rendered = collaboratorDeleteTime(fmt.Sprint(value))
		default:
			switch v := value.(type) {
			case string:
				rendered = v
			case json.Number:
				rendered = v.String()
			default:
				return fmt.Errorf("%w: %s.%s value", ErrCollaboratorDeleteUnsupported, model.Label, field.Name)
			}
		}
		changes[field.Name] = [2]string{rendered, "None"}
	}
	representation := model.ObjectName + " object (" + row.key + ")"
	switch model.Label {
	case collaboratorDeleteUser:
		first, _ := row.stored["first_name"].(string)
		last, _ := row.stored["last_name"].(string)
		representation = "name: " + first + " " + last
	case collaboratorDeleteCourseSubscription:
		representation = dto.CourseSubscriptionRepr(*row.course, dto.CourseSubscriptionMemory{})
	}
	var objectID *int64
	if model.PKType != "UUIDField" {
		id, err := strconv.ParseInt(row.key, 10, 64)
		if err != nil {
			return err
		}
		objectID = &id
	}
	association, path, err := r.indexAssociation(ctx, changes)
	if err != nil {
		return err
	}
	return appendModelAuditForApp(ctx, r.tx, model.AppLabel, modelAuditEntry{Model: model.ModelName, Key: row.key, Representation: representation,
		Action: 2, Changes: changes, Context: r.audit, AssociationID: association, ResolutionPath: path,
		At: r.now().UTC().Truncate(time.Microsecond), ObjectID: objectID})
}

// collaboratorDeleteTime is str(datetime.time) of PostgreSQL's time text.
func collaboratorDeleteTime(text string) string {
	main, fraction, found := strings.Cut(text, ".")
	if !found {
		return main
	}
	return main + "." + (fraction + "000000")[:6]
}

func (r *collaboratorDeleteRun) indexAssociation(ctx context.Context, changes map[string][2]string) (*string, *string, error) {
	if pair, ok := changes["sport_association"]; ok {
		for _, candidate := range pair {
			if candidate == "" || strings.EqualFold(candidate, "none") {
				continue
			}
			if _, err := uuid.Parse(candidate); err != nil {
				// SportAssociation.objects.filter raises; the receiver falls back.
				break
			}
			var found *string
			if err := r.tx.GetContext(ctx, &found, `SELECT (SELECT sport_association_id::text FROM application_sportassociation
 WHERE sport_association_id = $1::uuid AND NOT deleted)`, candidate); err != nil {
				return nil, nil, err
			}
			if found != nil {
				path := "changes_json.sport_association"
				return found, &path, nil
			}
		}
	}
	if r.audit.ActorID == nil {
		return nil, nil, nil
	}
	var found *string
	if err := r.tx.GetContext(ctx, &found, `SELECT (SELECT sport_association_id::text FROM application_sportassociation
 WHERE user_id = $1::uuid AND NOT deleted ORDER BY sport_association_id LIMIT 1)`, *r.audit.ActorID); err != nil {
		return nil, nil, err
	}
	if found == nil {
		return nil, nil, nil
	}
	path := "actor.sport_association"
	return found, &path, nil
}
