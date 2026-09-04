package orders

import (
	"context"
	"testing"
)

type countingRepository struct {
	order     Order
	getErr    error
	saveCount int
}

func (r *countingRepository) Get(context.Context, string) (Order, error) {
	return r.order, r.getErr
}

func (r *countingRepository) Save(_ context.Context, order Order) error {
	r.order = order
	r.saveCount++
	return nil
}

type countingNotifier struct {
	count int
}

func (n *countingNotifier) OrderCancelled(context.Context, string) error {
	n.count++
	return nil
}

func TestBenchmarkOracleServiceCancellationIsIdempotent(t *testing.T) {
	repository := &countingRepository{order: Order{ID: "order-1", Status: StatusPending}}
	notifier := &countingNotifier{}
	service := NewService(repository, notifier)

	first, err := service.Cancel(context.Background(), "order-1")
	if err != nil || first.Status != StatusCancelled {
		t.Fatalf("first cancellation: order=%#v err=%v", first, err)
	}
	second, err := service.Cancel(context.Background(), "order-1")
	if err != nil || second.Status != StatusCancelled {
		t.Fatalf("second cancellation: order=%#v err=%v", second, err)
	}
	if repository.saveCount != 1 {
		t.Fatalf("save count: got %d, want 1", repository.saveCount)
	}
	if notifier.count != 1 {
		t.Fatalf("notification count: got %d, want 1", notifier.count)
	}
}
