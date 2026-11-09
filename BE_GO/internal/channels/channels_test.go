package channels

import (
	"bufio"
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"math"
	"net"
	"os"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// golden is testdata/channels_redis_group_send.json, captured from the real
// GoMirroredRedisChannelLayer by TESTS/adapters/go/channels_group_send_golden.py.
type golden struct {
	ChannelsRedis string `json:"channels_redis"`
	Cases         []struct {
		Name           string            `json:"name"`
		Group          string            `json:"group"`
		Message        tagged            `json:"message"`
		ClockNS        []int64           `json:"clock_ns"`
		RandomPrefixes []string          `json:"random_prefixes"`
		Commands       [][]goldenArg     `json:"commands"`
		Replies        []json.RawMessage `json:"replies"`
		Envelope       goldenArg         `json:"envelope"`
	} `json:"cases"`
	Vectors []struct {
		Name string `json:"name"`
		Spec struct {
			MapNone  *int              `json:"map_none"`
			ListNone *int              `json:"list_none"`
			Repeat   []json.RawMessage `json:"repeat"`
			Value    *tagged           `json:"value"`
		} `json:"spec"`
		Packb goldenArg `json:"packb"`
	} `json:"msgpack_vectors"`
	TimeRepr   [][2]string `json:"time_repr"`
	GroupNames [][2]any    `json:"group_names"`
}

type tagged struct {
	T string          `json:"t"`
	V json.RawMessage `json:"v"`
}

// goldenArg is one byte string: inline base64, or length and SHA-256.
type goldenArg struct {
	B64    *string `json:"b64"`
	Len    int     `json:"len"`
	SHA256 string  `json:"sha256"`
}

func (a goldenArg) matches(t *testing.T, got []byte) bool {
	t.Helper()
	if a.B64 != nil {
		want, err := base64.StdEncoding.DecodeString(*a.B64)
		if err != nil {
			t.Fatal(err)
		}
		return bytes.Equal(want, got)
	}
	sum := sha256.Sum256(got)
	return len(got) == a.Len && hex.EncodeToString(sum[:]) == a.SHA256
}

func (v tagged) value(t *testing.T) any {
	t.Helper()
	switch v.T {
	case "none":
		return nil
	case "bool":
		var b bool
		mustUnmarshal(t, v.V, &b)
		return b
	case "str":
		var s string
		mustUnmarshal(t, v.V, &s)
		return s
	case "int":
		var text string
		mustUnmarshal(t, v.V, &text)
		if n, err := strconv.ParseInt(text, 10, 64); err == nil {
			return n
		}
		n, err := strconv.ParseUint(text, 10, 64)
		if err != nil {
			t.Fatal(err)
		}
		return n
	case "list":
		var items []tagged
		mustUnmarshal(t, v.V, &items)
		list := []any{}
		for _, item := range items {
			list = append(list, item.value(t))
		}
		return list
	case "dict":
		var items [][2]json.RawMessage
		mustUnmarshal(t, v.V, &items)
		dict := Dict{}
		for _, item := range items {
			var key string
			var inner tagged
			mustUnmarshal(t, item[0], &key)
			mustUnmarshal(t, item[1], &inner)
			dict = append(dict, Item{Key: key, Value: inner.value(t)})
		}
		return dict
	}
	t.Fatalf("unknown tag %q", v.T)
	return nil
}

func mustUnmarshal(t *testing.T, data []byte, target any) {
	t.Helper()
	if err := json.Unmarshal(data, target); err != nil {
		t.Fatal(err)
	}
}

func loadGolden(t *testing.T) golden {
	t.Helper()
	data, err := os.ReadFile("testdata/channels_redis_group_send.json")
	if err != nil {
		t.Fatal(err)
	}
	var g golden
	mustUnmarshal(t, data, &g)
	if g.ChannelsRedis != "4.2.1" || len(g.Cases) == 0 {
		t.Fatalf("unexpected golden: channels_redis %q, %d cases", g.ChannelsRedis, len(g.Cases))
	}
	return g
}

// fakeRedis is a RESP2 server that records every command after go-redis's
// connection handshake and answers each with reply.
type fakeRedis struct {
	t        *testing.T
	listener net.Listener
	mu       sync.Mutex
	commands [][][]byte
	reply    func(args [][]byte) string
}

func newFakeRedis(t *testing.T, reply func(args [][]byte) string) (*fakeRedis, *redis.Client) {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	fake := &fakeRedis{t: t, listener: listener, reply: reply}
	go fake.serve()
	client := redis.NewClient(&redis.Options{Addr: listener.Addr().String(), Protocol: 2, MaxRetries: -1})
	t.Cleanup(func() {
		client.Close()
		listener.Close()
	})
	return fake, client
}

func (f *fakeRedis) serve() {
	for {
		conn, err := f.listener.Accept()
		if err != nil {
			return
		}
		go f.handle(conn)
	}
}

func (f *fakeRedis) handle(conn net.Conn) {
	defer conn.Close()
	reader := bufio.NewReader(conn)
	for {
		args, err := readCommand(reader)
		if err != nil {
			return
		}
		var answer string
		switch strings.ToUpper(string(args[0])) {
		case "HELLO":
			answer = "-ERR unknown command 'HELLO'\r\n"
		case "CLIENT", "AUTH", "SELECT":
			answer = "+OK\r\n"
		default:
			f.mu.Lock()
			f.commands = append(f.commands, args)
			f.mu.Unlock()
			answer = f.reply(args)
		}
		if _, err := io.WriteString(conn, answer); err != nil {
			return
		}
	}
}

func (f *fakeRedis) recorded() [][][]byte {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([][][]byte(nil), f.commands...)
}

func readCommand(reader *bufio.Reader) ([][]byte, error) {
	line, err := reader.ReadString('\n')
	if err != nil {
		return nil, err
	}
	if line[0] != '*' {
		return nil, fmt.Errorf("not an array: %q", line)
	}
	count, err := strconv.Atoi(strings.TrimSpace(line[1:]))
	if err != nil {
		return nil, err
	}
	args := make([][]byte, count)
	for i := range args {
		header, err := reader.ReadString('\n')
		if err != nil {
			return nil, err
		}
		size, err := strconv.Atoi(strings.TrimSpace(header[1:]))
		if err != nil {
			return nil, err
		}
		body := make([]byte, size+2)
		if _, err := io.ReadFull(reader, body); err != nil {
			return nil, err
		}
		args[i] = body[:size]
	}
	return args, nil
}

// encodeReply renders a reply recorded from redis-py: OK/QUEUED were status
// replies, other byte strings bulk strings.
func encodeReply(t *testing.T, raw json.RawMessage) string {
	var reply struct {
		Int   *int64            `json:"int"`
		Bytes *string           `json:"bytes"`
		Array []json.RawMessage `json:"array"`
		Nil   bool              `json:"nil"`
	}
	mustUnmarshal(t, raw, &reply)
	switch {
	case reply.Int != nil:
		return ":" + strconv.FormatInt(*reply.Int, 10) + "\r\n"
	case reply.Bytes != nil:
		data, _ := base64.StdEncoding.DecodeString(*reply.Bytes)
		if string(data) == "OK" || string(data) == "QUEUED" {
			return "+" + string(data) + "\r\n"
		}
		return "$" + strconv.Itoa(len(data)) + "\r\n" + string(data) + "\r\n"
	case reply.Nil:
		return "$-1\r\n"
	default:
		out := "*" + strconv.Itoa(len(reply.Array)) + "\r\n"
		for _, element := range reply.Array {
			out += encodeReply(t, element)
		}
		return out
	}
}

func fixedClock(t *testing.T, ns []int64) (func() time.Time, func() int) {
	var mu sync.Mutex
	next := 0
	return func() time.Time {
			mu.Lock()
			defer mu.Unlock()
			if next >= len(ns) {
				t.Errorf("clock read %d times, golden has %d", next+1, len(ns))
				return time.Unix(0, ns[len(ns)-1])
			}
			next++
			return time.Unix(0, ns[next-1])
		}, func() int {
			mu.Lock()
			defer mu.Unlock()
			return next
		}
}

func TestGroupSendMatchesChannelsRedis(t *testing.T) {
	for _, tc := range loadGolden(t).Cases {
		t.Run(tc.Name, func(t *testing.T) {
			var replyMu sync.Mutex
			replyIndex := 0
			fake, client := newFakeRedis(t, func([][]byte) string {
				replyMu.Lock()
				defer replyMu.Unlock()
				if replyIndex >= len(tc.Replies) {
					return "-ERR unexpected command\r\n"
				}
				replyIndex++
				return encodeReply(t, tc.Replies[replyIndex-1])
			})
			now, reads := fixedClock(t, tc.ClockNS)
			var prefixes []byte
			for _, prefix := range tc.RandomPrefixes {
				raw, err := hex.DecodeString(prefix)
				if err != nil {
					t.Fatal(err)
				}
				prefixes = append(prefixes, raw...)
			}
			random := bytes.NewReader(prefixes)
			message, ok := tc.Message.value(t).(Dict)
			if !ok {
				t.Fatal("golden message is not a dict")
			}
			if err := NewLayer(client, now, random, nil).GroupSend(context.Background(), tc.Group, message); err != nil {
				t.Fatal(err)
			}
			if reads() != len(tc.ClockNS) || random.Len() != 0 {
				t.Fatalf("clock reads %d/%d, unread random bytes %d", reads(), len(tc.ClockNS), random.Len())
			}
			got := fake.recorded()
			if len(got) != len(tc.Commands) {
				t.Fatalf("%d commands, want %d: %q", len(got), len(tc.Commands), commandNames(got))
			}
			for i, want := range tc.Commands {
				if len(got[i]) != len(want) {
					t.Fatalf("command %d %q: %d args, want %d", i, got[i][0], len(got[i]), len(want))
				}
				for j, arg := range want {
					if !arg.matches(t, got[i][j]) {
						t.Fatalf("command %d %q arg %d differs: %q", i, got[i][0], j, truncate(got[i][j]))
					}
				}
			}
			last := got[len(got)-1]
			if string(last[0]) != "PUBLISH" || !tc.Envelope.matches(t, last[2]) {
				t.Fatalf("envelope differs from the subscriber's: %q", truncate(last[2]))
			}
		})
	}
}

func commandNames(commands [][][]byte) []string {
	names := make([]string, len(commands))
	for i, command := range commands {
		names[i] = string(command[0])
	}
	return names
}

func truncate(b []byte) []byte {
	if len(b) > 200 {
		return b[:200]
	}
	return b
}

func TestMsgpackMatchesPackb(t *testing.T) {
	g := loadGolden(t)
	for _, vector := range g.Vectors {
		var value any
		spec := vector.Spec
		switch {
		case spec.MapNone != nil:
			dict := make(Dict, *spec.MapNone)
			for i := range dict {
				dict[i] = Item{Key: strconv.Itoa(i)}
			}
			value = dict
		case spec.ListNone != nil:
			value = make([]any, *spec.ListNone)
		case spec.Repeat != nil:
			var text string
			var count int
			mustUnmarshal(t, spec.Repeat[0], &text)
			mustUnmarshal(t, spec.Repeat[1], &count)
			value = strings.Repeat(text, count)
		default:
			value = spec.Value.value(t)
		}
		if err := validateValue(value); err != nil {
			t.Fatalf("%s: %v", vector.Name, err)
		}
		if got := appendMsgpack(nil, value); !vector.Packb.matches(t, got) {
			t.Errorf("%s: msgpack differs: % x", vector.Name, truncate(got))
		}
	}
}

func TestMsgpackWidths(t *testing.T) {
	cases := []struct {
		value any
		want  string
	}{
		{0, "00"}, {127, "7f"}, {128, "cc80"}, {255, "ccff"}, {256, "cd0100"}, {65535, "cdffff"},
		{65536, "ce00010000"}, {int64(math.MaxUint32), "ceffffffff"}, {int64(math.MaxUint32) + 1, "cf0000000100000000"},
		{int64(math.MaxInt64), "cf7fffffffffffffff"}, {uint64(math.MaxUint64), "cfffffffffffffffff"}, {uint64(5), "05"},
		{-1, "ff"}, {-32, "e0"}, {-33, "d0df"}, {-128, "d080"}, {-129, "d1ff7f"}, {-32768, "d18000"},
		{-32769, "d2ffff7fff"}, {int64(math.MinInt32), "d280000000"}, {int64(math.MinInt32) - 1, "d3ffffffff7fffffff"},
		{int64(math.MinInt64), "d38000000000000000"},
		{nil, "c0"}, {true, "c3"}, {false, "c2"}, {"", "a0"},
		{strings.Repeat("a", 31), "bf" + strings.Repeat("61", 31)},
		{strings.Repeat("a", 32), "d920" + strings.Repeat("61", 32)},
		{[]any{}, "90"}, {Dict{}, "80"}, {Dict{{"a", []any{1, "b"}}}, "81a16192" + "01a162"},
	}
	for _, tc := range cases {
		if got := hex.EncodeToString(appendMsgpack(nil, tc.value)); got != tc.want {
			t.Errorf("%#v: got %s, want %s", tc.value, got, tc.want)
		}
	}
	for _, n := range []struct {
		size int
		head string
	}{{255, "d9ff"}, {256, "da0100"}, {65535, "daffff"}, {65536, "db00010000"}} {
		if got := hex.EncodeToString(appendMsgpack(nil, strings.Repeat("x", n.size))[:len(n.head)/2]); got != n.head {
			t.Errorf("str %d header %s, want %s", n.size, got, n.head)
		}
	}
	for _, n := range []struct {
		size       int
		list, dict string
	}{{15, "9f", "8f"}, {16, "dc0010", "de0010"}, {65535, "dcffff", "deffff"}, {65536, "dd00010000", "df00010000"}} {
		dict := make(Dict, n.size)
		for i := range dict {
			dict[i].Key = strconv.Itoa(i)
		}
		if got := hex.EncodeToString(appendMsgpack(nil, make([]any, n.size))[:len(n.list)/2]); got != n.list {
			t.Errorf("array %d header %s, want %s", n.size, got, n.list)
		}
		if got := hex.EncodeToString(appendMsgpack(nil, dict)[:len(n.dict)/2]); got != n.dict {
			t.Errorf("map %d header %s, want %s", n.size, got, n.dict)
		}
	}
}

func TestTimeReprMatchesPython(t *testing.T) {
	for _, pair := range loadGolden(t).TimeRepr {
		ns, err := strconv.ParseInt(pair[0], 10, 64)
		if err != nil {
			t.Fatal(err)
		}
		if got := pythonTimeRepr(time.Unix(0, ns)); got != pair[1] {
			t.Errorf("%d ns: got %s, want %s", ns, got, pair[1])
		}
	}
}

func TestGroupNamesMatchPython(t *testing.T) {
	for _, pair := range loadGolden(t).GroupNames {
		name, valid := pair[0].(string), pair[1].(bool)
		if err := validGroupName(name); (err == nil) != valid {
			t.Errorf("%q: valid=%v, Python %v", name, err == nil, valid)
		}
	}
}

// okReplies answers a group with one plain channel, as Redis would.
func okReplies(fail string) func(args [][]byte) string {
	return func(args [][]byte) string {
		name := string(args[0])
		if name == fail {
			return "-ERR injected failure\r\n"
		}
		switch name {
		case "ZRANGE":
			return "*1\r\n$7\r\nplain.a\r\n"
		case "MULTI":
			return "+OK\r\n"
		case "EXEC":
			return "*1\r\n:0\r\n"
		case "ZREMRANGEBYSCORE":
			return ":0\r\n"
		default:
			return ":0\r\n"
		}
	}
}

var notification = Dict{{"type", "notification_push"}, {"notification", Dict{{"id", "x"}}}}

func TestInvalidInputRefusedBeforeAnyCommand(t *testing.T) {
	fake, client := newFakeRedis(t, okReplies(""))
	layer := NewLayer(client, nil, nil, nil)
	for _, group := range []string{"", "a b", "a!b", "é", strings.Repeat("x", 100), "abc\n\n", "\xff"} {
		if err := layer.GroupSend(context.Background(), group, notification); !errors.Is(err, ErrInvalidGroupName) {
			t.Errorf("%q: %v", group, err)
		}
	}
	for _, message := range []Dict{
		{{"type", 1.5}}, {{"type", "\xff"}}, {{"\xff", 1}}, {{"a", 1}, {"a", 2}}, {{"a", []any{map[string]any{}}}}, {{"a", []byte("x")}},
	} {
		if err := layer.GroupSend(context.Background(), "group", message); !errors.Is(err, ErrUnsupportedValue) {
			t.Errorf("%v: %v", message, err)
		}
	}
	if got := fake.recorded(); len(got) != 0 {
		t.Fatalf("commands issued: %q", commandNames(got))
	}
}

type logRecorder struct {
	mu      sync.Mutex
	records []string
}

func (r *logRecorder) Enabled(context.Context, slog.Level) bool { return true }
func (r *logRecorder) Handle(_ context.Context, record slog.Record) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.records = append(r.records, record.Level.String()+" "+record.Message)
	return nil
}
func (r *logRecorder) WithAttrs([]slog.Attr) slog.Handler { return r }
func (r *logRecorder) WithGroup(string) slog.Handler      { return r }

func TestMirrorFailureIsLoggedNotReturned(t *testing.T) {
	fake, client := newFakeRedis(t, okReplies("PUBLISH"))
	logs := &logRecorder{}
	if err := NewLayer(client, nil, nil, slog.New(logs)).GroupSend(context.Background(), "group", notification); err != nil {
		t.Fatal(err)
	}
	names := commandNames(fake.recorded())
	if strings.Join(names, " ") != "ZREMRANGEBYSCORE ZRANGE MULTI ZREMRANGEBYSCORE EXEC EVAL PUBLISH" {
		t.Fatalf("commands %q", names)
	}
	if len(logs.records) != 1 || logs.records[0] != "WARN Unable to mirror a group message for Go WebSockets" {
		t.Fatalf("logs %q", logs.records)
	}
}

func TestChannelsFailureReturnedWithoutMirror(t *testing.T) {
	for _, fail := range []string{"ZRANGE", "EXEC", "EVAL"} {
		fake, client := newFakeRedis(t, okReplies(fail))
		if err := NewLayer(client, nil, nil, nil).GroupSend(context.Background(), "group", notification); err == nil {
			t.Fatalf("%s failure not returned", fail)
		}
		for _, name := range commandNames(fake.recorded()) {
			if name == "PUBLISH" {
				t.Fatalf("%s failure still mirrored", fail)
			}
		}
	}
}

func TestOverCapacityIsLogged(t *testing.T) {
	_, client := newFakeRedis(t, func(args [][]byte) string {
		if string(args[0]) == "EVAL" {
			return ":1\r\n"
		}
		return okReplies("")(args)
	})
	logs := &logRecorder{}
	if err := NewLayer(client, nil, nil, slog.New(logs)).GroupSend(context.Background(), "full", notification); err != nil {
		t.Fatal(err)
	}
	if len(logs.records) != 1 || logs.records[0] != "INFO 1 of 1 channels over capacity in group full" {
		t.Fatalf("logs %q", logs.records)
	}
}

func TestContextCancellation(t *testing.T) {
	fake, client := newFakeRedis(t, okReplies(""))
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if err := NewLayer(client, nil, nil, nil).GroupSend(ctx, "group", notification); !errors.Is(err, context.Canceled) {
		t.Fatalf("cancelled send: %v", err)
	}
	if got := fake.recorded(); len(got) != 0 {
		t.Fatalf("commands after cancellation: %q", commandNames(got))
	}

	// Cancelled while ZRANGE is in flight: no channel delivery and no mirror.
	ctx, cancel = context.WithCancel(context.Background())
	defer cancel()
	fake, client = newFakeRedis(t, func(args [][]byte) string {
		if string(args[0]) == "ZRANGE" {
			cancel()
		}
		return okReplies("")(args)
	})
	if err := NewLayer(client, nil, nil, nil).GroupSend(ctx, "group", notification); !errors.Is(err, context.Canceled) {
		t.Fatalf("send cancelled mid-way: %v", err)
	}
	if names := commandNames(fake.recorded()); strings.Join(names, " ") != "ZREMRANGEBYSCORE ZRANGE" {
		t.Fatalf("commands %q", names)
	}
}

func TestRandomFailureStopsBeforeDelivery(t *testing.T) {
	fake, client := newFakeRedis(t, okReplies(""))
	if err := NewLayer(client, nil, bytes.NewReader([]byte{1, 2, 3}), nil).GroupSend(context.Background(), "group", notification); err == nil {
		t.Fatal("short random source accepted")
	}
	if names := commandNames(fake.recorded()); strings.Join(names, " ") != "ZREMRANGEBYSCORE ZRANGE" {
		t.Fatalf("commands %q", names)
	}
}
