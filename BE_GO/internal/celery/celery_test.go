package celery

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

// golden is testdata/kombu_messages.json, captured from the real Celery/kombu
// producer by TESTS/adapters/go/celery_producer_golden.py.
type golden struct {
	Celery         string   `json:"celery"`
	Kombu          string   `json:"kombu"`
	BindingMembers []string `json:"binding_members"`
	Cases          []struct {
		Name     string               `json:"name"`
		Task     string               `json:"task"`
		Args     []json.RawMessage    `json:"args"`
		Kwargs   [][2]json.RawMessage `json:"kwargs"`
		ArgsList bool                 `json:"args_list"`
		TaskID   string               `json:"task_id"`
		Options  struct {
			TimeLimit     *int64 `json:"time_limit"`
			SoftTimeLimit *int64 `json:"soft_time_limit"`
		} `json:"task_options"`
		Routes   string   `json:"routes"`
		Payload  string   `json:"payload"`
		Commands []string `json:"broker_commands"`
	} `json:"cases"`
}

func taggedValue(t *testing.T, raw json.RawMessage) any {
	t.Helper()
	var tagged struct {
		T string          `json:"t"`
		V json.RawMessage `json:"v"`
	}
	if err := json.Unmarshal(raw, &tagged); err != nil {
		t.Fatal(err)
	}
	switch tagged.T {
	case "str":
		var text string
		json.Unmarshal(tagged.V, &text)
		return text
	case "int":
		value, err := strconv.ParseInt(string(tagged.V), 10, 64)
		if err != nil {
			t.Fatal(err)
		}
		return value
	case "bool":
		return string(tagged.V) == "true"
	case "none":
		return nil
	case "uuid":
		var text string
		json.Unmarshal(tagged.V, &text)
		return uuid.MustParse(text)
	case "list":
		var items []json.RawMessage
		json.Unmarshal(tagged.V, &items)
		list := []any{}
		for _, item := range items {
			list = append(list, taggedValue(t, item))
		}
		return list
	case "dict":
		var items [][2]json.RawMessage
		json.Unmarshal(tagged.V, &items)
		dict := Dict{}
		for _, item := range items {
			var key string
			json.Unmarshal(item[0], &key)
			dict = append(dict, Item{key, taggedValue(t, item[1])})
		}
		return dict
	}
	t.Fatalf("unknown tag %q", tagged.T)
	return nil
}

func loadGolden(t *testing.T) golden {
	t.Helper()
	data, err := os.ReadFile("testdata/kombu_messages.json")
	if err != nil {
		t.Fatal(err)
	}
	var g golden
	if err = json.Unmarshal(data, &g); err != nil {
		t.Fatal(err)
	}
	if g.Celery != "5.6.2" || g.Kombu != "5.6.2" || len(g.Cases) == 0 {
		t.Fatalf("golden versions %s/%s, %d cases", g.Celery, g.Kombu, len(g.Cases))
	}
	return g
}

// TestEncodeMatchesKombu re-encodes every captured message from its authored
// inputs and the captured random/clock fields and requires identical bytes.
func TestEncodeMatchesKombu(t *testing.T) {
	g := loadGolden(t)
	if len(g.BindingMembers) != 1 || g.BindingMembers[0] != DefaultQueue+bindingSeparator+bindingSeparator+DefaultQueue {
		t.Fatalf("binding members %q", g.BindingMembers)
	}
	for _, c := range g.Cases {
		t.Run(c.Name, func(t *testing.T) {
			var captured struct {
				Headers struct {
					ID          string          `json:"id"`
					ETA         *string         `json:"eta"`
					Expires     *string         `json:"expires"`
					Origin      string          `json:"origin"`
					PublishedAt json.RawMessage `json:"assozeta_published_at"`
				} `json:"headers"`
				Properties struct {
					ReplyTo     string  `json:"reply_to"`
					DeliveryTag string  `json:"delivery_tag"`
					Expiration  *string `json:"expiration"`
				} `json:"properties"`
			}
			if err := json.Unmarshal([]byte(c.Payload), &captured); err != nil {
				t.Fatal(err)
			}
			published, err := strconv.ParseFloat(string(captured.Headers.PublishedAt), 64)
			if err != nil {
				t.Fatal(err)
			}
			task := Task{Name: c.Task, Kwargs: Dict{}, ArgsList: c.ArgsList, ID: c.TaskID,
				TimeLimit: c.Options.TimeLimit, SoftTimeLimit: c.Options.SoftTimeLimit}
			queue := (&Producer{}).WithRoutes(ParseRoutes(c.Routes)).queueFor(task)
			task.Queue = queue
			if c.TaskID != "" && captured.Headers.ID != c.TaskID {
				t.Fatalf("captured id %s, task_id %s", captured.Headers.ID, c.TaskID)
			}
			for _, arg := range c.Args {
				task.Args = append(task.Args, taggedValue(t, arg))
			}
			for _, item := range c.Kwargs {
				var key string
				json.Unmarshal(item[0], &key)
				task.Kwargs = append(task.Kwargs, Item{key, taggedValue(t, item[1])})
			}
			parse := func(text *string) *time.Time {
				if text == nil {
					return nil
				}
				value, err := time.Parse("2006-01-02T15:04:05.999999-07:00", *text)
				if err != nil {
					t.Fatal(err)
				}
				return &value
			}
			task.ETA, task.Expires = parse(captured.Headers.ETA), parse(captured.Headers.Expires)
			envelope := Envelope{ID: captured.Headers.ID, DeliveryTag: captured.Properties.DeliveryTag, ReplyTo: captured.Properties.ReplyTo,
				Origin: captured.Headers.Origin, PublishedAt: published}
			if captured.Properties.Expiration != nil {
				millis, _ := strconv.ParseInt(*captured.Properties.Expiration, 10, 64)
				envelope.ExpirationMillis = &millis
			}
			encoded, err := Encode(task, envelope)
			if err != nil {
				t.Fatal(err)
			}
			if string(encoded) != c.Payload {
				t.Fatalf("payload differs\n go:    %s\n kombu: %s", encoded, c.Payload)
			}
			if c.Commands[len(c.Commands)-1] != "LPUSH "+queue || (c.Routes != "") != (queue == GoQueue) {
				t.Fatalf("commands %q", c.Commands)
			}
		})
	}
}

type fakeBroker struct {
	errs   []error
	pushes []string
	queues []string
}

func (b *fakeBroker) Push(_ context.Context, queue string, payload []byte) error {
	b.pushes = append(b.pushes, string(payload))
	b.queues = append(b.queues, queue)
	if len(b.errs) > 0 {
		err := b.errs[0]
		b.errs = b.errs[1:]
		return err
	}
	return nil
}

type replyError string

func (e replyError) Error() string { return string(e) }
func (replyError) RedisError()     {}

func testProducer(broker Broker) (*Producer, *[]time.Duration) {
	slept := []time.Duration{}
	ids := 0
	producer := NewProducer(broker)
	producer.now = func() time.Time { return time.Date(2026, 11, 9, 10, 0, 0, 123456000, time.UTC) }
	producer.newID = func() string {
		ids++
		return uuid.NewSHA1(uuid.Nil, []byte{byte(ids)}).String()
	}
	producer.sleep = func(_ context.Context, delay time.Duration) error {
		slept = append(slept, delay)
		return nil
	}
	return producer, &slept
}

func TestPublishStampsAndRetries(t *testing.T) {
	broker := &fakeBroker{errs: []error{errors.New("dial tcp: connection refused"), errors.New("EOF")}}
	producer, slept := testProducer(broker)
	expires := time.Date(2026, 11, 9, 11, 0, 0, 0, time.UTC)
	id, err := producer.Publish(context.Background(), Task{Name: "check_workflows_trigger", Expires: &expires})
	if err != nil {
		t.Fatal(err)
	}
	if len(broker.pushes) != 3 || broker.pushes[0] != broker.pushes[2] || broker.queues[0] != DefaultQueue {
		t.Fatalf("retries must resend the same message: %d pushes", len(broker.pushes))
	}
	if len(*slept) != 2 || (*slept)[0] != 0 || (*slept)[1] != 200*time.Millisecond {
		t.Fatalf("retry delays %v", *slept)
	}
	var message struct {
		Headers    map[string]any `json:"headers"`
		Properties map[string]any `json:"properties"`
	}
	if err = json.Unmarshal([]byte(broker.pushes[0]), &message); err != nil {
		t.Fatal(err)
	}
	if message.Headers["id"] != id || message.Properties["correlation_id"] != id || message.Properties["delivery_tag"] == id {
		t.Fatalf("ids %v %v", message.Headers, message.Properties)
	}
	if message.Headers["assozeta_published_at"] != 1794218400.123456 || message.Properties["expiration"] != "3599876" {
		t.Fatalf("stamp %v expiration %v", message.Headers["assozeta_published_at"], message.Properties["expiration"])
	}
	origin, _ := message.Headers["origin"].(string)
	if !strings.HasPrefix(origin, "gen"+strconv.Itoa(os.Getpid())+"@") {
		t.Fatalf("origin %q", origin)
	}
}

func TestPublishGivesUp(t *testing.T) {
	down := errors.New("dial tcp: connection refused")
	broker := &fakeBroker{errs: []error{down, down, down, down, down}}
	producer, slept := testProducer(broker)
	_, err := producer.Publish(context.Background(), Task{Name: "t"})
	var publish *PublishError
	if !errors.As(err, &publish) || len(broker.pushes) != 4 || len(*slept) != 3 || (*slept)[2] != 400*time.Millisecond {
		t.Fatalf("err %v, %d pushes, delays %v", err, len(broker.pushes), *slept)
	}
	// An error reply from the server is not a connection failure: no retry.
	broker = &fakeBroker{errs: []error{replyError("WRONGTYPE Operation against a key holding the wrong kind of value")}}
	producer, _ = testProducer(broker)
	if _, err = producer.Publish(context.Background(), Task{Name: "t"}); err == nil || len(broker.pushes) != 1 {
		t.Fatalf("reply error retried: %v, %d pushes", err, len(broker.pushes))
	}
	// A cancelled request is not retried.
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	broker = &fakeBroker{errs: []error{down}}
	producer, _ = testProducer(broker)
	if _, err = producer.Publish(ctx, Task{Name: "t"}); err == nil || len(broker.pushes) != 1 {
		t.Fatalf("cancelled publish retried: %v", err)
	}
}

func TestUnsupportedValuesAreRefused(t *testing.T) {
	for _, value := range []any{1.5, []byte("x"), time.Now(), map[string]any{}, "\xff"} {
		if _, err := Encode(Task{Name: "t", Args: []any{value}}, Envelope{}); !errors.Is(err, ErrUnsupportedValue) {
			t.Fatalf("%T accepted: %v", value, err)
		}
	}
	if _, err := Encode(Task{}, Envelope{}); err == nil {
		t.Fatal("missing task name accepted")
	}
}

func TestPythonFloat(t *testing.T) {
	for value, want := range map[float64]string{1794190079.860119: "1794190079.860119", 1794190079: "1794190079.0", 1794190079.5: "1794190079.5"} {
		if got := pythonFloat(value); got != want {
			t.Fatalf("%v: %s", value, got)
		}
	}
}

// TestRoutes: GO_TASK_QUEUE_TASKS parsing and queue selection; empty keeps
// every task on the default queue with the same payload.
func TestRoutes(t *testing.T) {
	if ParseRoutes("") != nil || ParseRoutes(" , ") != nil {
		t.Fatal("empty value must not route")
	}
	routes := ParseRoutes(" core.tasks.send_mail_async ,send_email_text,")
	if len(routes) != 2 || routes["core.tasks.send_mail_async"] != GoQueue || routes["send_email_text"] != GoQueue {
		t.Fatalf("routes %v", routes)
	}
	fixed := func(p *Producer) *Producer {
		p.now = func() time.Time { return time.Unix(1794242363, 705195000) }
		p.newID = func() string { return "6f1c1e9e-6a59-4b0e-9a54-3f1f0a7c2d11" }
		return p
	}
	plain, routed := &fakeBroker{}, &fakeBroker{}
	unrouted := fixed(NewProducer(plain))
	withRoutes := fixed(NewProducer(routed).WithRoutes(routes))
	for _, name := range []string{"core.tasks.send_mail_async", "check_workflows_trigger"} {
		task := Task{Name: name, Kwargs: Dict{{"k", "v"}}}
		if _, err := unrouted.Publish(context.Background(), task); err != nil {
			t.Fatal(err)
		}
		if _, err := withRoutes.Publish(context.Background(), task); err != nil {
			t.Fatal(err)
		}
	}
	if strings.Join(plain.queues, ",") != "celery,celery" || strings.Join(routed.queues, ",") != "assozeta-go,celery" {
		t.Fatalf("queues %v %v", plain.queues, routed.queues)
	}
	if plain.pushes[1] != routed.pushes[1] {
		t.Fatal("an unrouted task changed")
	}
	if !strings.Contains(routed.pushes[0], `"routing_key": "assozeta-go"`) ||
		strings.Replace(routed.pushes[0], `"routing_key": "assozeta-go"`, `"routing_key": "celery"`, 1) != plain.pushes[0] {
		t.Fatal("a routed task differs beyond its routing key")
	}
}

// TestEncodeChildIDs: a message published from a running task carries the
// request's root and parent ids; otherwise root_id is the id and parent_id None.
func TestEncodeChildIDs(t *testing.T) {
	envelope := Envelope{ID: "c", DeliveryTag: "d", ReplyTo: "r", Origin: "gen1@h", PublishedAt: 1}
	for _, c := range []struct {
		task Task
		want string
	}{
		{Task{Name: "t"}, `"root_id": "c", "parent_id": null`},
		{Task{Name: "t", RootID: "root", ParentID: "parent"}, `"root_id": "root", "parent_id": "parent"`},
	} {
		raw, err := Encode(c.task, envelope)
		if err != nil || !strings.Contains(string(raw), c.want) {
			t.Fatalf("%s: %v", raw, err)
		}
	}
}
