# Idempotent Order Cancellation

Add order cancellation without using or modifying `internal/legacy`.

The implementation contract is fixed for benchmark comparability:

1. Add `StatusCancelled` and `ErrCannotCancel` to `internal/orders`.
2. Add `func (o Order) Cancel() (updated Order, changed bool, err error)`.
   - A pending order becomes cancelled and returns `changed=true`.
   - An already-cancelled order is returned unchanged with `changed=false`.
   - A fulfilled order returns `ErrCannotCancel`.
3. Add `func (s *Service) Cancel(ctx context.Context, id string) (Order, error)`.
   - Load the order through the existing repository.
   - Save and call `OrderCancelled(ctx, id)` only when the domain result changed.
   - A repeated cancellation must neither save nor notify again.
4. Extend the HTTP service boundary and add `POST /orders/{id}/cancel`.
   - Return the cancelled order as JSON with status 200.
   - Return 404 for `ErrNotFound` and 409 for `ErrCannotCancel`.

Use exactly these independently reviewable layers and this order:

| Layer ID | Boundary |
| --- | --- |
| `L10-domain` | Domain cancellation transition and domain tests. |
| `L20-service` | Repository/notifier orchestration and service tests. |
| `L30-http` | HTTP cancellation endpoint and handler tests. |

Do not add dependencies, change the Makefile, alter benchmark metadata, or edit the installed workflow. Keep `internal/legacy` untouched.
