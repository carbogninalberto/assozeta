package celery

import (
	"encoding/base64"
	"errors"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

// TestEnvelopeRoundTrip: every captured kombu payload decodes and re-encodes
// to the same bytes (what kombu writes to the unacked hash and restores).
func TestEnvelopeRoundTrip(t *testing.T) {
	for _, c := range loadGolden(t).Cases {
		m, err := ParseMessage([]byte(c.Payload))
		if err != nil {
			t.Fatalf("%s: %v", c.Name, err)
		}
		again, err := EncodeJSON(m.Envelope)
		if err != nil || string(again) != c.Payload {
			t.Fatalf("%s: round trip differs: %v", c.Name, err)
		}
		if m.Task != c.Task || m.PublishedAt == nil || m.DeliveryTag == "" {
			t.Fatalf("%s: decoded %+v", c.Name, m)
		}
	}
}

func goldenCase(t *testing.T, name string) string {
	for _, c := range loadGolden(t).Cases {
		if c.Name == name {
			return c.Payload
		}
	}
	t.Fatalf("no golden case %s", name)
	return ""
}

// TestEncodeRepublish follows signature_from_request(...).apply_async() of a
// top-level message: same id, args and kwargs, root and parent ids set to
// the task id, the request's origin and reply_to, the restore stamp kept as
// written, and the queue taken from the routing key.
func TestEncodeRepublish(t *testing.T) {
	raw := goldenCase(t, "routed-to-go-queue")
	m, err := ParseMessage([]byte(raw))
	if err != nil {
		t.Fatal(err)
	}
	if !m.Plain() || m.RepublishQueue() != GoQueue {
		t.Fatal("plain routed message expected")
	}
	eta := time.Date(2026, 11, 9, 16, 39, 23, 705195000, time.UTC)
	payload, err := EncodeRepublish(m, Republish{Retries: 1, ETA: eta, DeliveryTag: "00000000-0000-4000-8000-000000000001", Origin: "gen1@x"})
	if err != nil {
		t.Fatal(err)
	}
	again, err := ParseMessage(payload)
	if err != nil {
		t.Fatal(err)
	}
	stamp, _ := m.Headers.Get("assozeta_published_at")
	keys := func(d Dict) string {
		var out []string
		for _, item := range d {
			out = append(out, item.Key)
		}
		return strings.Join(out, ",")
	}
	if keys(again.Headers) != strings.Join(standardHeaders, ",")+",assozeta_published_at" {
		t.Fatalf("header order %s", keys(again.Headers))
	}
	if keys(again.Properties) != "correlation_id,reply_to,origin,delivery_mode,delivery_info,priority,body_encoding,delivery_tag" {
		t.Fatalf("property order %s", keys(again.Properties))
	}
	newStamp, _ := again.Headers.Get("assozeta_published_at")
	origin, _ := again.Properties.Get("origin")
	argsrepr, _ := again.Headers.Get("argsrepr")
	if again.ID != m.ID || again.Retries != 1 || again.RootID != m.ID || again.ParentID != m.ID || newStamp != stamp ||
		again.ReplyTo != m.ReplyTo || again.Origin != m.Origin || origin != m.Origin || argsrepr != "()" ||
		!again.ETA.Equal(eta) || again.RoutingKey != GoQueue || again.DeliveryTag != "00000000-0000-4000-8000-000000000001" {
		t.Fatalf("republished %s", payload)
	}
	body, _ := m.Envelope.Get("body")
	newBody, _ := again.Envelope.Get("body")
	if body != newBody {
		t.Fatal("body changed")
	}
	// A child's parent and root ids are kept.
	m.ParentID, m.RootID = "parent", "root"
	payload, _ = EncodeRepublish(m, Republish{Retries: 2, ETA: eta, DeliveryTag: "t"})
	again, _ = ParseMessage(payload)
	if again.ParentID != "parent" || again.RootID != "root" {
		t.Fatal("child ids not kept")
	}
}

func TestParseRefusals(t *testing.T) {
	raw := goldenCase(t, "routed-to-go-queue")
	body := func(text string) string {
		m, _ := ParseMessage([]byte(raw))
		old, _ := m.Envelope.Get("body")
		return strings.Replace(raw, old.(string), base64.StdEncoding.EncodeToString([]byte(text)), 1)
	}
	for name, payload := range map[string]string{
		"pickle":         strings.Replace(raw, `"application/json"`, `"application/x-python-serialize"`, 1),
		"lone surrogate": strings.Replace(raw, `"lang": "py"`, `"lang": "py", "x": "\ud800"`, 1),
		"datetime":       body(`[[{"__type__": "datetime", "__value__": "2026-01-01T00:00:00"}], {}, {"callbacks": null, "errbacks": null, "chain": null, "chord": null}]`),
		"chain":          body(`[[], {}, {"callbacks": null, "errbacks": null, "chain": [1], "chord": null}]`),
		"naive eta":      strings.Replace(raw, `"eta": null`, `"eta": "2026-01-01T00:00:00"`, 1),
	} {
		if _, err := ParseMessage([]byte(payload)); !errors.Is(err, ErrUndecodable) {
			t.Errorf("%s: %v", name, err)
		}
	}
	m, err := ParseMessage([]byte(body(`[[{"__type__": "uuid", "__value__": {"hex": "6f1c1e9e6a594b0e9a543f1f0a7c2d11"}}], {}, {"callbacks": null, "errbacks": null, "chain": null, "chord": null}]`)))
	if err != nil || m.Args[0] != uuid.MustParse("6f1c1e9e-6a59-4b0e-9a54-3f1f0a7c2d11") {
		t.Fatalf("uuid argument: %v", err)
	}
	expiring, _ := ParseMessage([]byte(strings.Replace(raw, `"expires": null`, `"expires": "2026-01-01T00:00:00+00:00"`, 1)))
	if expiring == nil || expiring.Plain() {
		t.Fatal("a message with expires is not republished by this package")
	}
}
