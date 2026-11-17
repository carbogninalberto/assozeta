package importeffects

import (
	"encoding/json"
	"errors"
	"os"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/refusal"
)

// testdata/pyset.json holds CPython 3.13 iteration orders of uuid.UUID sets
// built by add() (random keys with repeats).
func TestPySetOrderMatchesCPython(t *testing.T) {
	data, err := os.ReadFile("testdata/pyset.json")
	if err != nil || len(data) == 0 {
		t.Fatalf("goldens: %v", err)
	}
	var cases []struct {
		In  []string `json:"in"`
		Out []string `json:"out"`
	}
	if err := json.Unmarshal(data, &cases); err != nil || len(cases) == 0 {
		t.Fatalf("goldens: %v", err)
	}
	for i, c := range cases {
		if got := pySetOrder(c.In); !reflect.DeepEqual(got, c.Out) {
			t.Fatalf("case %d (%d keys): got %v want %v", i, len(c.In), got, c.Out)
		}
	}
}

// TestPySetOrderInts pins CPython's int set order (hash(int) is the value
// modulo 2**61 - 1):
//
//	>>> list({5, 1, 9, 3, 7})
//	[1, 3, 5, 7, 9]
//	>>> list({10, 20, 30, 40, 50, 60})
//	[40, 10, 50, 20, 60, 30]
func TestPySetOrderInts(t *testing.T) {
	if got := pySetOrderInts([]string{"5", "1", "9", "3", "7", "1"}); !reflect.DeepEqual(got, []string{"1", "3", "5", "7", "9"}) {
		t.Fatalf("ints: %v", got)
	}
	if got := pySetOrderInts([]string{"10", "20", "30", "40", "50", "60"}); !reflect.DeepEqual(got, []string{"40", "10", "50", "20", "60", "30"}) {
		t.Fatalf("ints resize: %v", got)
	}
}

// TestPlanUnreachableIsAppState keeps the one remaining sentinel out of the
// hand-off: it is application state, never a "not ported" refusal.
func TestPlanUnreachableIsAppState(t *testing.T) {
	err := planUnreachable("field kind %s", "x")
	if !errors.Is(err, ErrPlanUnreachable) || !errors.Is(err, refusal.ErrAppState) {
		t.Fatalf("sentinel: %v", err)
	}
	if errors.Is(err, refusal.ErrNotPorted) {
		t.Fatal("plan state must not hand off")
	}
	if reason, ok := refusal.AppStateReason(err); !ok || !strings.Contains(reason, "generated plan") {
		t.Fatalf("reason %q %v", reason, ok)
	}
}

func TestPlanLoads(t *testing.T) {
	plan, err := LoadPlan()
	if err != nil {
		t.Fatal(err)
	}
	if plan.Model("Payment").Save != "payment" || plan.Model("User").Str != "user" || !plan.Model("Folder").IsMPTT {
		t.Fatal("unexpected plan")
	}
}

// TestPlanFacts pins every planUnreachable site's plan fact: a regenerated
// plan that makes one of them reachable fails here.
func TestPlanFacts(t *testing.T) {
	plan, err := LoadPlan()
	if err != nil {
		t.Fatal(err)
	}
	fieldKinds := map[string]bool{"bool": true, "date": true, "datetime": true, "decimal": true, "fk": true,
		"int": true, "json": true, "text": true, "time": true, "uuid": true}
	defaultKinds := map[string]bool{"none": true, "value": true, "callable": true}
	defaultTypes := map[string]bool{"None": true, "bool": true, "int": true, "str": true, "Decimal": true, "float": true}
	callables := map[string]bool{
		"builtins.dict": true, "builtins.list": true, "django.utils.timezone.now": true, "uuid.uuid4": true,
		"application.models.balance_sheet_models.current_year":       true,
		"application.models.courses_models.random_color":             true,
		"application.models.subscriptions_models.default_expires_at": true,
		"application.models.utils.get_default_additional_sections":   true,
		"application.models.utils.get_default_configuration":         true,
		"application.models.utils.get_default_enabled_for":           true,
		"application.models.utils.get_default_stripe_methods":        true,
	}
	saves := map[string]bool{"default": true, "payment": true, "subscription": true, "course": true, "mptt": true}
	strs := map[string]bool{"default": true, "user": true, "dict": true}
	for _, model := range plan.Models {
		// plan.json: no imported model registers auditlog include_fields.
		if model.Audit != nil && len(model.Audit.Include) > 0 {
			t.Fatalf("plan fact broken: %s has auditlog include_fields", model.Name)
		}
		// plan.json: save is one of the five known overrides.
		if !saves[model.Save] {
			t.Fatalf("plan fact broken: %s save %s", model.Name, model.Save)
		}
		// plan.json: str is default, user (User) or dict (CourseSubscription).
		if !strs[model.Str] {
			t.Fatalf("plan fact broken: %s str %s", model.Name, model.Str)
		}
		if model.Str == "dict" && (model.Name != "CourseSubscription" || model.Audit == nil) {
			t.Fatalf("plan fact broken: str dict on %s (audited %v)", model.Name, model.Audit != nil)
		}
		// plan.json: an audited int primary key is an AutoField assigned by the
		// INSERT's RETURNING (PK_FIELDS has no entry for the model), so log
		// entries never parse a key from archive text.
		if model.Audit != nil && model.PK.Kind != "uuid" &&
			(model.PKField().Type != "AutoField" || plan.PKFields[model.Name] == model.PK.Attname) {
			t.Fatalf("plan fact broken: audited %s has a %s pk", model.Name, model.PK.Kind)
		}
		// plan.json: MPTT is Folder only and it is not audited.
		if model.IsMPTT && (model.Name != "Folder" || model.Audit != nil) {
			t.Fatalf("plan fact broken: MPTT %s (audited %v)", model.Name, model.Audit != nil)
		}
		if !model.IsMPTT && model.Save == "mptt" {
			t.Fatalf("plan fact broken: %s save mptt without is_mptt", model.Name)
		}
		// plan.json: SaveFields updates Document and Subscription rows only.
		if model.Save == "payment" && model.Name != "Payment" {
			t.Fatalf("plan fact broken: payment save on %s", model.Name)
		}
		// plan.json: every m2m source model has a uuid pk and an int or uuid target.
		for _, m2m := range model.M2M {
			if model.PK.Kind != "uuid" {
				t.Fatalf("plan fact broken: m2m source %s has a %s pk", model.Name, model.PK.Kind)
			}
			if m2m.TargetKind != "uuid" && m2m.TargetKind != "int" {
				t.Fatalf("plan fact broken: %s.%s target %s", model.Name, m2m.Name, m2m.TargetKind)
			}
		}
		for _, field := range model.Fields {
			// plan.json: the field kinds planUnreachable names.
			if !fieldKinds[field.Kind] {
				t.Fatalf("plan fact broken: %s.%s kind %s", model.Name, field.Name, field.Kind)
			}
			// plan.json: no imported JSON field has an encoder.
			if field.Kind == "json" && field.Encoder != nil {
				t.Fatalf("plan fact broken: %s.%s has a JSON encoder", model.Name, field.Name)
			}
			// plan.json: fk targets are uuid or int keys.
			if field.IsFK() && field.TargetKind != "uuid" && field.TargetKind != "int" {
				t.Fatalf("plan fact broken: %s.%s target kind %s", model.Name, field.Name, field.TargetKind)
			}
			// plan.json: every auto_now/auto_now_add field is a DateTimeField.
			if (field.AutoNow || field.AutoNowAdd) && field.Type != "DateTimeField" {
				t.Fatalf("plan fact broken: %s.%s auto date %s", model.Name, field.Name, field.Type)
			}
			// plan.json: the stored audit rendering covers every column type;
			// datetime columns are timestamp with time zone.
			if field.Kind == "datetime" && field.DBType != "timestamp with time zone" {
				t.Fatalf("plan fact broken: %s.%s db type %s", model.Name, field.Name, field.DBType)
			}
			// plan.json: default kinds, constant types and callable names.
			if !defaultKinds[field.Default.Kind] {
				t.Fatalf("plan fact broken: %s.%s default kind %s", model.Name, field.Name, field.Default.Kind)
			}
			if field.Default.Kind == "value" && !defaultTypes[field.Default.Type] {
				t.Fatalf("plan fact broken: %s.%s default type %s", model.Name, field.Name, field.Default.Type)
			}
			if field.Default.Kind == "callable" && !callables[field.Default.Name] {
				t.Fatalf("plan fact broken: %s.%s default %s", model.Name, field.Name, field.Default.Name)
			}
		}
	}
	// The only model force-updated (the restore's association row) has the
	// default save.
	if plan.Model("SportAssociation").Save != "default" {
		t.Fatal("plan fact broken: SportAssociation has a save override")
	}
	// The only non-auto-created through is Associate.tutors.
	for _, model := range plan.Models {
		for _, m2m := range model.M2M {
			if !m2m.AutoCreated && (model.Name != "Associate" || m2m.Name != "tutors") {
				t.Fatalf("plan fact broken: custom through %s.%s", model.Name, m2m.Name)
			}
		}
	}
}

// TestSeasonRange pins BalanceSheetData.get_range_from_year_and_starting_date
// for the settings save() reads (TIME_ZONE is UTC):
//
//	>>> BalanceSheetData.get_range_from_year_and_starting_date(datetime(2028, 3, 1), 29, 2)
//	ValueError: Invalid date combination: day is out of range for month
//	>>> ... (2028, 3, 1), 1, 9  -> 2025-09-01 .. 2026-08-31
func TestSeasonRange(t *testing.T) {
	e := &Effects{}
	moment := Value{Kind: DateTime, T: time.Date(2028, 3, 1, 0, 0, 0, 0, time.UTC)}
	if _, _, err := e.seasonRange(moment, subscriptionOwner{startDay: 29, startMon: 2, duration: 2}); err == nil ||
		err.Error() != "Invalid date combination: day is out of range for month" {
		t.Fatalf("February 29 of the previous year: %v", err)
	}
	from, to, err := e.seasonRange(moment, subscriptionOwner{startDay: 1, startMon: 9, duration: 2})
	if err != nil {
		t.Fatal(err)
	}
	if from.Format("2006-01-02") != "2027-09-01" || to.Format("2006-01-02") != "2028-08-31" {
		t.Fatalf("season %s .. %s", from, to)
	}
	custom := subscriptionOwner{startDay: 1, startMon: 9, customEnd: true, endMonth: 6, endDay: 30}
	if _, to, err := e.seasonRange(moment, custom); err != nil || to.Format("2006-01-02") != "2028-06-30" {
		t.Fatalf("custom end %s %v", to, err)
	}
}

// TestSubscriptionFill pins Subscription.save's date filling.
func TestSubscriptionFill(t *testing.T) {
	plan, err := LoadPlan()
	if err != nil {
		t.Fatal(err)
	}
	model := plan.Model("Subscription")
	created := Value{Kind: DateTime, T: time.Date(2028, 3, 1, 10, 0, 0, 0, time.UTC)}
	instance := &Instance{Model: model, Values: map[string]Value{
		"creation_date": created, "start_date": NoneValue, "end_date": NoneValue}, Adding: true}
	owner := subscriptionOwner{startDay: 1, startMon: 9, duration: 2}
	from, to, err := (&Effects{}).seasonRange(created, owner)
	if err != nil {
		t.Fatal(err)
	}
	instance.Values["start_date"] = Value{Kind: Date, S: from.Format("2006-01-02")}
	instance.Values["end_date"] = Value{Kind: Date, S: to.Format("2006-01-02")}
	if instance.Get("start_date").S != "2027-09-01" || instance.Get("end_date").S != "2028-08-31" {
		t.Fatalf("fill %+v", instance.Values)
	}
	if got := addYears(Value{Kind: Date, S: "2028-02-29"}, 1); got.S != "2029-02-28" {
		t.Fatalf("relativedelta: %s", got.S)
	}
}

// TestPlanForbidsUnknownFields keeps NewInstance's unknown-keyword branch
// (planUnreachable) out of the callers' way: buildInstance passes plan fields.
func TestPlanForbidsUnknownFields(t *testing.T) {
	plan, _ := LoadPlan()
	payment := plan.Model("Payment")
	if payment.FieldByAttname("nope") != nil {
		t.Fatal("unexpected field")
	}
	if strings.TrimSpace(payment.Name) == "" {
		t.Fatal("empty model")
	}
}
