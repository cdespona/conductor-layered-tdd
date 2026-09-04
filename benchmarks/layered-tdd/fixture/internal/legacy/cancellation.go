package legacy

// CancelOrder is intentionally disconnected legacy behavior. The benchmark
// request explicitly forbids using or modifying this package.
func CancelOrder(status string) string {
	if status == "pending" {
		return "cancelled"
	}
	return status
}
