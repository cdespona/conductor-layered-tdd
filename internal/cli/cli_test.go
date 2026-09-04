package cli

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestInstallAndValidateCommands(t *testing.T) {
	target := t.TempDir()
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	if err := Run([]string{"install", "--target", target, "--memory-skills"}, &stdout, &stderr); err != nil {
		t.Fatalf("install: %v; stderr=%s", err, stderr.String())
	}
	if !strings.Contains(stdout.String(), "LTDD install complete") {
		t.Fatalf("stdout = %q", stdout.String())
	}
	if _, err := os.Stat(filepath.Join(target, "workflows", "conductor", "layered-tdd.yaml")); err != nil {
		t.Fatal(err)
	}

	stdout.Reset()
	stderr.Reset()
	oldPath := os.Getenv("PATH")
	t.Setenv("PATH", "")
	if err := Run([]string{"validate", "--target", target}, &stdout, &stderr); err != nil {
		t.Fatalf("validate: %v; stderr=%s", err, stderr.String())
	}
	if !strings.Contains(stdout.String(), "LTDD structure: valid") {
		t.Fatalf("stdout = %q", stdout.String())
	}
	t.Setenv("PATH", oldPath)
}

func TestVersion(t *testing.T) {
	var stdout bytes.Buffer
	if err := Run([]string{"version"}, &stdout, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	if !strings.HasPrefix(stdout.String(), "ltdd ") {
		t.Fatalf("stdout = %q", stdout.String())
	}
}
