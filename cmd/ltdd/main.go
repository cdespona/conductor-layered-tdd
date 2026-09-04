package main

import (
	"fmt"
	"os"

	"github.com/cdespona/conductor-layered-tdd/internal/cli"
)

var version = "dev"

func main() {
	cli.Version = version
	if err := cli.Run(os.Args[1:], os.Stdout, os.Stderr); err != nil {
		fmt.Fprintf(os.Stderr, "Error: %v\n", err)
		os.Exit(1)
	}
}
