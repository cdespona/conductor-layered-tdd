package orders

import "errors"

type Status string

const (
	StatusPending   Status = "pending"
	StatusFulfilled Status = "fulfilled"
)

var ErrNotFound = errors.New("order not found")

type Order struct {
	ID     string `json:"id"`
	Status Status `json:"status"`
}
