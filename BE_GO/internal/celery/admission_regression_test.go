package celery

import (
	"bytes"
	"context"
	"database/sql"
	"database/sql/driver"
	"fmt"
	"io"
	"strings"
	"testing"

	"github.com/redis/go-redis/v9"
)

// Exercise PublishAttempt with an installed ledger and an accepting Go owner.
func TestPublishAttemptPreservesLoneSurrogate(t *testing.T) {
	var raw []byte
	for _, c := range loadGolden(t).Cases {
		if c.Name == "lone-surrogate" {
			raw = []byte(c.Payload)
			break
		}
	}
	if !bytes.Contains(raw, []byte(`\ud800`)) || !bytes.Contains(raw, []byte(`\udfff`)) {
		t.Fatal("missing surrogate header fixture")
	}
	// A valid extra field may look like the route, and its spelling matters.
	raw = bytes.Replace(raw, []byte(`"headers": {`), []byte(`"headers": { "routing_key": "celery", "extra": 1.00e+0,`), 1)
	want := bytes.Replace(raw, []byte(`"exchange": "", "routing_key": "celery"`), []byte(`"exchange": "", "routing_key": "assozeta-go"`), 1)
	if bytes.Equal(want, raw) {
		t.Fatal("missing delivery routing key in fixture")
	}
	ledger := new(admissionTestLedger)
	db := sql.OpenDB(ledger)
	t.Cleanup(func() { _ = db.Close() })
	pushes := new(admissionTestRedis)
	broker := AdmittingBroker{DB: db, Redis: RedisBroker{Client: pushes}}
	if err := broker.PublishAttempt(t.Context(), DefaultQueue, raw, ""); err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(ledger.original, raw) || ledger.queue != GoQueue || !bytes.Equal(ledger.routed, want) {
		t.Fatal("admission changed envelope bytes outside the routing key")
	}
	if pushes.queue != GoQueue || !bytes.Equal(pushes.payload, want) {
		t.Fatal("Redis did not receive the exact routed envelope")
	}
}

type admissionTestLedger struct {
	original, routed []byte
	queue            string
}

func (l *admissionTestLedger) Connect(context.Context) (driver.Conn, error) { return l, nil }
func (*admissionTestLedger) Driver() driver.Driver                          { return nil }
func (*admissionTestLedger) Prepare(string) (driver.Stmt, error)            { return nil, driver.ErrSkip }
func (*admissionTestLedger) Close() error                                   { return nil }
func (l *admissionTestLedger) Begin() (driver.Tx, error)                    { return l, nil }
func (*admissionTestLedger) Commit() error                                  { return nil }
func (*admissionTestLedger) Rollback() error                                { return nil }
func (l *admissionTestLedger) QueryContext(_ context.Context, q string, a []driver.NamedValue) (driver.Rows, error) {
	switch {
	case strings.Contains(q, "assozeta_handoff_admit("):
		l.original = bytes.Clone(a[3].Value.([]byte))
		return &admissionTestRow{[]driver.Value{"accepted", true}}, nil
	case strings.Contains(q, "to_regclass("), strings.Contains(q, "FOR SHARE OF o"):
		return &admissionTestRow{[]driver.Value{true}}, nil
	default:
		return nil, fmt.Errorf("unexpected admission query: %s", q)
	}
}
func (l *admissionTestLedger) ExecContext(_ context.Context, q string, a []driver.NamedValue) (driver.Result, error) {
	if !strings.Contains(q, "UPDATE public.assozeta_handoff_work") {
		return nil, fmt.Errorf("unexpected admission update: %s", q)
	}
	l.queue = a[1].Value.(string)
	l.routed = bytes.Clone(a[2].Value.([]byte))
	return driver.RowsAffected(1), nil
}

type admissionTestRow struct{ values []driver.Value }

func (r *admissionTestRow) Columns() []string { return make([]string, len(r.values)) }
func (*admissionTestRow) Close() error        { return nil }
func (r *admissionTestRow) Next(dest []driver.Value) error {
	if r.values == nil {
		return io.EOF
	}
	copy(dest, r.values)
	r.values = nil
	return nil
}

type admissionTestRedis struct {
	redis.Cmdable
	queue   string
	payload []byte
}

func (r *admissionTestRedis) TxPipelined(_ context.Context, f func(redis.Pipeliner) error) ([]redis.Cmder, error) {
	client := redis.NewClient(&redis.Options{})
	defer client.Close()
	pipe := client.Pipeline()
	if err := f(pipe); err != nil {
		return nil, err
	}
	for _, cmd := range pipe.Cmds() {
		if cmd.Name() == "lpush" {
			r.queue = cmd.Args()[1].(string)
			r.payload = cmd.Args()[2].([]byte)
		}
	}
	return pipe.Cmds(), nil
}
