package orders

import (
	"context"

	"benchmark.local/order-service/internal/notification"
)

type Service struct {
	repository Repository
	notifier   notification.CancellationNotifier
}

func NewService(repository Repository, notifier notification.CancellationNotifier) *Service {
	return &Service{repository: repository, notifier: notifier}
}

func (s *Service) Get(ctx context.Context, id string) (Order, error) {
	return s.repository.Get(ctx, id)
}
