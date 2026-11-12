package service

import "testing"

func TestSubscriptionCreateGroupCanonicalisesAndKeepsTheSentText(t *testing.T) {
	t.Run("no header", func(t *testing.T) {
		stored, sent := subscriptionCreateGroup("  ")
		if stored != nil || sent != "" {
			t.Fatalf("blank header: stored=%v sent=%q", stored, sent)
		}
	})
	t.Run("canonical spelling", func(t *testing.T) {
		const header = "a1000000-0000-4000-8000-000000714001"
		stored, sent := subscriptionCreateGroup(header)
		if stored == nil || *stored != header || sent != header {
			t.Fatalf("canonical header: stored=%v sent=%q", stored, sent)
		}
	})
	t.Run("other spelling", func(t *testing.T) {
		stored, sent := subscriptionCreateGroup("A1000000-0000-4000-8000-000000714001")
		if stored == nil || *stored != "a1000000-0000-4000-8000-000000714001" ||
			sent != "A1000000-0000-4000-8000-000000714001" {
			t.Fatalf("other spelling: stored=%v sent=%q", stored, sent)
		}
	})
	t.Run("not a uuid", func(t *testing.T) {
		stored, sent := subscriptionCreateGroup("zzz")
		if stored == nil || *stored != "zzz" || sent != "zzz" {
			t.Fatalf("not a uuid: stored=%v sent=%q", stored, sent)
		}
	})
}
