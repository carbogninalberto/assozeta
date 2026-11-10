package repository

import (
	"encoding/hex"
	"testing"

	"github.com/carbogninalberto/assozeta/BE_GO/internal/domain"
)

// pickle.dumps({'total': 0.31, 'stripe_fee': 0.31}, 4) on CPython 3.13.
func TestPicklePaymentStripeChargesMatchesCPython(t *testing.T) {
	want := "8004952c000000000000007d94288c05746f74616c94473fd3d70a3d70a3d78c0a7374726970655f66656594473fd3d70a3d70a3d7752e"
	got := hex.EncodeToString(picklePaymentStripeCharges(domain.PaymentStripeCharges{Total: 0.31, StripeFee: 0.31}))
	if got != want {
		t.Fatalf("pickle = %s, want %s", got, want)
	}
	value, err := unpickleDataRestoreProgress(picklePaymentStripeCharges(domain.PaymentStripeCharges{Total: 12.65, StripeFee: 2.5}))
	dict, ok := value.(*PickledDict)
	if err != nil || !ok || dict.Values["total"] != 12.65 || dict.Values["stripe_fee"] != 2.5 {
		t.Fatalf("round trip = %#v, %v", value, err)
	}
}
