package httpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"benchmark.local/order-service/internal/orders"
)

type oracleNotifier struct {
	count int
}

func (n *oracleNotifier) OrderCancelled(context.Context, string) error {
	n.count++
	return nil
}

func TestBenchmarkOracleHTTPCancellationIsIdempotent(t *testing.T) {
	repository := orders.NewMemoryRepository(orders.Order{ID: "order-1", Status: orders.StatusPending})
	notifier := &oracleNotifier{}
	handler := NewHandler(orders.NewService(repository, notifier))

	for attempt := 0; attempt < 2; attempt++ {
		request := httptest.NewRequest(http.MethodPost, "/orders/order-1/cancel", nil)
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)

		if response.Code != http.StatusOK {
			t.Fatalf("attempt %d status: got %d, want %d; body=%s", attempt+1, response.Code, http.StatusOK, response.Body.String())
		}
		var order orders.Order
		if err := json.NewDecoder(response.Body).Decode(&order); err != nil {
			t.Fatalf("attempt %d decode response: %v", attempt+1, err)
		}
		if order.Status != orders.StatusCancelled {
			t.Fatalf("attempt %d status: got %q, want %q", attempt+1, order.Status, orders.StatusCancelled)
		}
	}

	if notifier.count != 1 {
		t.Fatalf("notification count: got %d, want 1", notifier.count)
	}
}
