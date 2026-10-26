package dto

import (
	"encoding/json"
	"reflect"
	"testing"
	"time"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
	"github.com/google/uuid"
)

func TestMessageReadsProjectionPreservesDjangoEscapingAndTypedNulls(t *testing.T) {
	messageID := uuid.MustParse("a1000000-0000-4000-8000-000000702001")
	associationID := uuid.MustParse("a1000000-0000-4000-8000-000000701001")
	created := time.Date(2026, 10, 23, 8, 9, 10, 123456000, time.FixedZone("fixture", 2*60*60))
	subject := `  Quote & attività <2026> "quoted" 'single'  `
	value := MessageReadFromRecord(domain.MessageReadRecord{
		ID: messageID, AssociationID: associationID, Type: "EMAIL",
		Message: `<p>A & B "quoted" 'single'</p>`, Subject: &subject, CreatedAt: &created,
	})
	if value.Message != `&lt;p&gt;A &amp; B &quot;quoted&quot; &#x27;single&#x27;&lt;/p&gt;` {
		t.Fatal(value.Message)
	}
	if value.Subject != `  Quote &amp; attività &lt;2026&gt; &quot;quoted&quot; &#x27;single&#x27;  ` {
		t.Fatal(value.Subject)
	}
	if value.CreatedAt == nil || *value.CreatedAt != "2026-10-23T06:09:10.123456Z" {
		t.Fatal(value.CreatedAt)
	}
	encoded, err := json.Marshal(value)
	if err != nil || string(encoded) != `{"message_id":"a1000000-0000-4000-8000-000000702001","sport_association":"a1000000-0000-4000-8000-000000701001","type":"EMAIL","message":"\u0026lt;p\u0026gt;A \u0026amp; B \u0026quot;quoted\u0026quot; \u0026#x27;single\u0026#x27;\u0026lt;/p\u0026gt;","subject":"  Quote \u0026amp; attività \u0026lt;2026\u0026gt; \u0026quot;quoted\u0026quot; \u0026#x27;single\u0026#x27;  ","created_at":"2026-10-23T06:09:10.123456Z"}` {
		t.Fatal(string(encoded), err)
	}

	nullSubject := MessageReadFromRecord(domain.MessageReadRecord{
		ID: messageID, AssociationID: associationID, Type: "INSIDE_APP", Message: "Post",
	})
	if nullSubject.Subject != "None" || nullSubject.CreatedAt != nil {
		t.Fatal(nullSubject)
	}
}

func TestMessageTransactionReadsPreservesEveryStoredField(t *testing.T) {
	id := uuid.MustParse("a1000000-0000-4000-8000-000000703001")
	messageID := uuid.MustParse("a1000000-0000-4000-8000-000000702001")
	sent := time.Date(2026, 10, 23, 12, 0, 0, 0, time.UTC)
	delivered := sent.Add(time.Second + 123456*time.Microsecond)
	reason, kind, status := "Mailbox full", "permanent", "5.2.2"
	value := MessageTransactionReadFromRecord(domain.MessageTransactionReadRecord{
		ID: id, MessageID: messageID, Recipient: "person@example.test", SentOn: sent,
		DeliveredOn: &delivered, BouncedReason: &reason, BouncedType: &kind, BouncedStatus: &status,
	})
	if value.ID != id || value.MessageID != messageID || value.Recipient != "person@example.test" ||
		value.SentOn != "2026-10-23T12:00:00Z" || value.DeliveredOn == nil ||
		*value.DeliveredOn != "2026-10-23T12:00:01.123456Z" ||
		value.BouncedReason == nil || *value.BouncedReason != reason ||
		value.BouncedType == nil || *value.BouncedType != kind ||
		value.BouncedStatus == nil || *value.BouncedStatus != status ||
		value.OpenedOn != nil || value.ClickedOn != nil || value.BouncedOn != nil {
		t.Fatalf("%+v", value)
	}
	encoded, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	var decoded map[string]any
	if err = json.Unmarshal(encoded, &decoded); err != nil {
		t.Fatal(err)
	}
	expected := map[string]any{
		"message_transaction_id": id.String(),
		"message":                messageID.String(),
		"recipient":              "person@example.test",
		"sent_on":                "2026-10-23T12:00:00Z",
		"delivered_on":           "2026-10-23T12:00:01.123456Z",
		"opened_on":              nil,
		"clicked_on":             nil,
		"bounced_on":             nil,
		"bounced_reason":         reason,
		"bounced_type":           kind,
		"bounced_status":         status,
	}
	if !reflect.DeepEqual(decoded, expected) {
		t.Fatalf("decoded transaction = %#v, want %#v", decoded, expected)
	}
	if _, present := decoded["message_id"]; present {
		t.Fatal("Django transaction JSON must not contain message_id")
	}
}
