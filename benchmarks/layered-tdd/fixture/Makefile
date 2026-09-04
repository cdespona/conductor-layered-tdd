.PHONY: test test-noisy lint audit

test:
	go test -v ./...

test-noisy:
	BENCH_NOISY=1 go test -v ./...

lint:
	go vet ./...

audit:
	go list -m -mod=readonly all >/dev/null
