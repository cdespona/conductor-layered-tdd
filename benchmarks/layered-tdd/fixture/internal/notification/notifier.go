package notification

import "context"

type CancellationNotifier interface {
	OrderCancelled(context.Context, string) error
}

type NopNotifier struct{}

func (NopNotifier) OrderCancelled(context.Context, string) error { return nil }
