.PHONY: build test vet validate

build:
	go build ./cmd/ltdd

test:
	go test ./...

vet:
	go vet ./...

validate:
	conductor validate bundle/workflows/conductor/layered-tdd.yaml
