package orders

import (
	"context"
	"sync"
)

type Repository interface {
	Get(context.Context, string) (Order, error)
	Save(context.Context, Order) error
}

type MemoryRepository struct {
	mu     sync.RWMutex
	orders map[string]Order
}

func NewMemoryRepository(initial ...Order) *MemoryRepository {
	repository := &MemoryRepository{orders: make(map[string]Order, len(initial))}
	for _, order := range initial {
		repository.orders[order.ID] = order
	}
	return repository
}

func (r *MemoryRepository) Get(_ context.Context, id string) (Order, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()

	order, ok := r.orders[id]
	if !ok {
		return Order{}, ErrNotFound
	}
	return order, nil
}

func (r *MemoryRepository) Save(_ context.Context, order Order) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.orders[order.ID] = order
	return nil
}
