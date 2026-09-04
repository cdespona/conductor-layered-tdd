package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"benchmark.local/order-service/internal/orders"
)

type OrderService interface {
	Get(context.Context, string) (orders.Order, error)
}

type Handler struct {
	service OrderService
}

func NewHandler(service OrderService) http.Handler {
	return &Handler{service: service}
}

func (h *Handler) ServeHTTP(response http.ResponseWriter, request *http.Request) {
	if request.Method != http.MethodGet {
		http.NotFound(response, request)
		return
	}

	id, ok := orderID(request.URL.Path)
	if !ok {
		http.NotFound(response, request)
		return
	}

	order, err := h.service.Get(request.Context(), id)
	if errors.Is(err, orders.ErrNotFound) {
		http.Error(response, err.Error(), http.StatusNotFound)
		return
	}
	if err != nil {
		http.Error(response, err.Error(), http.StatusInternalServerError)
		return
	}

	response.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(response).Encode(order)
}

func orderID(path string) (string, bool) {
	const prefix = "/orders/"
	if !strings.HasPrefix(path, prefix) {
		return "", false
	}
	id := strings.TrimPrefix(path, prefix)
	return id, id != "" && !strings.Contains(id, "/")
}
