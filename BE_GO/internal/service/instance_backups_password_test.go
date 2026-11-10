package service

import (
	"context"
	"sync"
	"testing"

	passwordcrypto "github.com/carbogninalberto/assozeta/BE_GO/pkg/crypto"
)

// backupsPasswordStore is the attempts cache with a scripted stale read: the
// get of the limit check may return an older count than the counter holds.
type backupsPasswordStore struct {
	mu      sync.Mutex
	count   int64
	present bool
	stale   *int64
	checked []int64 // the counter while the password was checked
}

func (f *backupsPasswordStore) Attempts(context.Context, string) (int64, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.stale != nil {
		return *f.stale, nil
	}
	return f.count, nil
}

func (f *backupsPasswordStore) CountAttempt(context.Context, string) (int64, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.present = true
	f.count++
	return f.count, nil
}

func (f *backupsPasswordStore) ClearAttempts(context.Context, string) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.count, f.present = 0, false
	return nil
}

func (f *backupsPasswordStore) Password(context.Context, string) (string, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.checked = append(f.checked, f.count)
	return "stored", nil
}

func (f *backupsPasswordStore) SetPassword(context.Context, string, string) error { return nil }

type backupsPasswordHasher struct{ right string }

func (h backupsPasswordHasher) CheckDjangoPassword(_ context.Context, value passwordcrypto.LoginPasswordValue, _ string) (passwordcrypto.LoginPasswordDecision, error) {
	return passwordcrypto.LoginPasswordDecision{Match: value.Text == h.right}, nil
}

func (h backupsPasswordHasher) Encode(context.Context, string) (string, error) {
	return "upgraded", nil
}

func TestInstanceBackupsPasswordTryIsCountedBeforeTheCheck(t *testing.T) {
	store := &backupsPasswordStore{count: instanceBackupsAttempts - 1, present: true}
	status, message, err := instanceBackupsPasswordRefusal(context.Background(), store, backupsPasswordHasher{"right"}, "actor", "wrong")
	if err != nil || status != 400 || message != "Password non corretta." {
		t.Fatalf("got %d %q %v", status, message, err)
	}
	if len(store.checked) != 1 || store.checked[0] != instanceBackupsAttempts || store.count != instanceBackupsAttempts {
		t.Fatalf("checked with %v, count %d", store.checked, store.count)
	}
}

func TestInstanceBackupsPasswordCountOverTheLimitIsRefusedWithoutCheck(t *testing.T) {
	// Simultaneous tries all read a count under the limit; the counted value decides.
	stale := int64(instanceBackupsAttempts - 1)
	store := &backupsPasswordStore{count: instanceBackupsAttempts, present: true, stale: &stale}
	status, message, err := instanceBackupsPasswordRefusal(context.Background(), store, backupsPasswordHasher{"right"}, "actor", "right")
	if err != nil || status != 429 || message != "Troppi tentativi. Riprova tra qualche minuto." {
		t.Fatalf("got %d %q %v", status, message, err)
	}
	if len(store.checked) != 0 || store.count != instanceBackupsAttempts+1 {
		t.Fatalf("checked %v, count %d", store.checked, store.count)
	}
}

func TestInstanceBackupsPasswordSimultaneousTriesStayWithinTheLimit(t *testing.T) {
	zero := int64(0)
	store := &backupsPasswordStore{stale: &zero}
	var wg sync.WaitGroup
	for range 20 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, _, err := instanceBackupsPasswordRefusal(context.Background(), store, backupsPasswordHasher{"right"}, "actor", "wrong"); err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	if len(store.checked) != instanceBackupsAttempts {
		t.Fatalf("%d passwords checked, want %d", len(store.checked), instanceBackupsAttempts)
	}
}

func TestInstanceBackupsPasswordMatchClearsTheCounter(t *testing.T) {
	store := &backupsPasswordStore{count: 2, present: true}
	status, _, err := instanceBackupsPasswordRefusal(context.Background(), store, backupsPasswordHasher{"right"}, "actor", "right")
	if err != nil || status != 0 || store.present || store.count != 0 {
		t.Fatalf("got %d %v, store %+v", status, err, store)
	}
	store = &backupsPasswordStore{count: instanceBackupsAttempts, present: true}
	if status, _, _ := instanceBackupsPasswordRefusal(context.Background(), store, backupsPasswordHasher{"right"}, "actor", "right"); status != 429 || len(store.checked) != 0 {
		t.Fatalf("at the limit: %d, checked %v", status, store.checked)
	}
}
