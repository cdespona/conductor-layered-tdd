package httpapi

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"

	"benchmark.local/order-service/internal/orders"
)

type stubService struct {
	order orders.Order
	err   error
}

func (s stubService) Get(context.Context, string) (orders.Order, error) {
	return s.order, s.err
}

func TestHandlerGetsOrder(t *testing.T) {
	handler := NewHandler(stubService{order: orders.Order{ID: "order-1", Status: orders.StatusPending}})
	request := httptest.NewRequest(http.MethodGet, "/orders/order-1", nil)
	response := httptest.NewRecorder()

	handler.ServeHTTP(response, request)

	if response.Code != http.StatusOK {
		t.Fatalf("status: got %d, want %d", response.Code, http.StatusOK)
	}
}
