package orders

import (
	"errors"
	"testing"
)

func TestBenchmarkOracleDomainCancellation(t *testing.T) {
	pending := Order{ID: "order-1", Status: StatusPending}
	cancelled, changed, err := pending.Cancel()
	if err != nil || !changed || cancelled.Status != StatusCancelled {
		t.Fatalf("first cancellation: order=%#v changed=%v err=%v", cancelled, changed, err)
	}

	again, changed, err := cancelled.Cancel()
	if err != nil || changed || again != cancelled {
		t.Fatalf("repeated cancellation: order=%#v changed=%v err=%v", again, changed, err)
	}

	_, changed, err = (Order{ID: "order-2", Status: StatusFulfilled}).Cancel()
	if !errors.Is(err, ErrCannotCancel) || changed {
		t.Fatalf("fulfilled cancellation: changed=%v err=%v", changed, err)
	}
}
