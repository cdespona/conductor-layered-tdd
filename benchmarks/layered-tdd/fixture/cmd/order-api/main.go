package main

import (
	"log"
	"net/http"

	"benchmark.local/order-service/internal/httpapi"
	"benchmark.local/order-service/internal/notification"
	"benchmark.local/order-service/internal/orders"
)

func main() {
	repository := orders.NewMemoryRepository()
	service := orders.NewService(repository, notification.NopNotifier{})
	handler := httpapi.NewHandler(service)

	log.Fatal(http.ListenAndServe(":8080", handler))
}
