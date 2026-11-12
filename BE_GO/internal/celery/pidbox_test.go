package celery

import (
	"context"
	"encoding/base64"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/redis/go-redis/v9"
)

// The broadcast envelope kombu 5.6 publishes for inspect().ping() (captured
// from Python with a fixed ticket, oid, delivery tag and clock).
func TestControlMessageBytes(t *testing.T) {
	now := time.Unix(1794294085, 149783100)
	raw, err := ControlMessage("ping", "706ba510-e3cd-4a85-9bdf-dd670049b29d", "45d59a82-2bbb-380d-b89b-458aa26a1ac7",
		"ec83b319-75d8-435d-8c60-a1867cf5f431", now, 2*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	want := `{"body": "eyJtZXRob2QiOiAicGluZyIsICJhcmd1bWVudHMiOiB7fSwgImRlc3RpbmF0aW9uIjogbnVsbCwgInBhdHRlcm4iOiBudWxsLCAibWF0Y2hlciI6IG51bGwsICJ0aWNrZXQiOiAiNzA2YmE1MTAtZTNjZC00YTg1LTliZGYtZGQ2NzAwNDliMjlkIiwgInJlcGx5X3RvIjogeyJleGNoYW5nZSI6ICJyZXBseS5jZWxlcnkucGlkYm94IiwgInJvdXRpbmdfa2V5IjogIjQ1ZDU5YTgyLTJiYmItMzgwZC1iODliLTQ1OGFhMjZhMWFjNyJ9fQ==", "content-encoding": "utf-8", "content-type": "application/json", "headers": {"clock": 1, "expires": 1794294087.1497831}, "properties": {"delivery_mode": 2, "delivery_info": {"exchange": "celery.pidbox", "routing_key": ""}, "priority": 0, "body_encoding": "base64", "delivery_tag": "ec83b319-75d8-435d-8c60-a1867cf5f431"}}`
	if string(raw) != want {
		t.Fatalf("envelope\n got %s\nwant %s", raw, want)
	}
}

// A worker's reply as kombu publishes it (captured).
func TestDecodeControlReply(t *testing.T) {
	reply := `{"body": "eyJzdHViQGhvc3QiOiB7Im9rIjogInBvbmcifX0=", "content-encoding": "utf-8", "content-type": "application/json", "headers": {"ticket": "4b779159-e37e-4a45-962d-1a2f2effd590", "clock": 3}, "properties": {"delivery_mode": 1, "delivery_info": {"exchange": "reply.celery.pidbox", "routing_key": "55633663-6f3b-3f76-82c4-fee85216a199"}, "priority": 0, "body_encoding": "base64", "delivery_tag": "8976fe2e-0c6e-4c5b-b822-546e50af21d1"}}`
	body, claimed, err := decodeControlReply([]byte(reply), "4b779159-e37e-4a45-962d-1a2f2effd590", time.Now())
	if err != nil || !claimed {
		t.Fatalf("claimed=%v err=%v", claimed, err)
	}
	encoded, _ := EncodeJSON(body)
	if string(encoded) != `{"stub@host": {"ok": "pong"}}` {
		t.Fatalf("body %s", encoded)
	}
	if _, claimed, _ = decodeControlReply([]byte(reply), "other", time.Now()); claimed {
		t.Fatal("another call's reply was claimed")
	}
	pickled := strings.Replace(reply, "application/json", "application/x-python-serialize", 1)
	if _, _, err = decodeControlReply([]byte(pickled), "x", time.Now()); err == nil {
		t.Fatal("non-JSON content accepted")
	}
	if _, err = base64.StdEncoding.DecodeString("eyJzdHViQGhvc3QiOiB7Im9rIjogInBvbmcifX0="); err != nil {
		t.Fatal(err)
	}
}

// CELERY_PIDBOX_TEST_REDIS=127.0.0.1:<port> runs a ping against whatever
// answers there (a Celery worker or kombu node on DB 0); CELERY_PIDBOX_EXPECT
// is the hostname that must answer pong.
func TestInspectPingLive(t *testing.T) {
	address := os.Getenv("CELERY_PIDBOX_TEST_REDIS")
	if address == "" {
		t.Skip("CELERY_PIDBOX_TEST_REDIS not set")
	}
	client := redis.NewClient(&redis.Options{Addr: address, Password: os.Getenv("CELERY_PIDBOX_TEST_PASSWORD"), DB: 0})
	defer client.Close()
	started := time.Now()
	b, err := Inspect(context.Background(), client, "ping", 2*time.Second, nil)
	if err != nil {
		t.Fatal(err)
	}
	elapsed := time.Since(started)
	t.Logf("replies after %s: %d", elapsed, len(b.Replies))
	expect := os.Getenv("CELERY_PIDBOX_EXPECT")
	found := false
	for _, reply := range b.Replies {
		encoded, _ := EncodeJSON(reply)
		t.Logf("reply %s", encoded)
		if d, ok := reply.(Dict); ok {
			if value, ok := d.Get(expect); ok {
				if inner, ok := value.(Dict); ok {
					if pong, _ := inner.Get("ok"); pong == "pong" {
						found = true
					}
				}
			}
		}
	}
	if !found {
		t.Fatalf("no pong from %q", expect)
	}
	if elapsed < 2*time.Second {
		t.Fatalf("collected for %s, want the 2 s quiet period", elapsed)
	}
	keys, err := client.Keys(context.Background(), "*reply.celery.pidbox*").Result()
	if err != nil || len(keys) != 0 {
		t.Fatalf("reply queue left behind: %v %v", keys, err)
	}
}
