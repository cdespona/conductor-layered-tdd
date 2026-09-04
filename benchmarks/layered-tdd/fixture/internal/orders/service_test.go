package orders

import (
	"context"
	"testing"

	"benchmark.local/order-service/internal/notification"
)

func TestServiceGetsExistingOrder(t *testing.T) {
	repository := NewMemoryRepository(Order{ID: "order-1", Status: StatusPending})
	service := NewService(repository, notification.NopNotifier{})

	order, err := service.Get(context.Background(), "order-1")
	if err != nil {
		t.Fatalf("get order: %v", err)
	}
	if order.ID != "order-1" || order.Status != StatusPending {
		t.Fatalf("unexpected order: %#v", order)
	}
}
