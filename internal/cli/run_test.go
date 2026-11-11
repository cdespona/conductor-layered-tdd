package cli

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// fakeConductor installs a shell script that records its arguments and working directory.
func fakeConductor(t *testing.T, body string) (string, string) {
	t.Helper()
	dir := t.TempDir()
	log := filepath.Join(dir, "args.log")
	script := filepath.Join(dir, "conductor")
	content := "#!/bin/sh\npwd > '" + log + "'\nfor a in \"$@\"; do printf '%s\\n' \"$a\" >> '" + log + "'; done\n" + body + "\n"
	if err := os.WriteFile(script, []byte(content), 0o755); err != nil {
		t.Fatal(err)
	}
	previous := lookPath
	lookPath = func(string) (string, error) { return script, nil }
	t.Cleanup(func() { lookPath = previous })
	return script, log
}

func installedTarget(t *testing.T) string {
	t.Helper()
	target := t.TempDir()
	if err := Run([]string{"install", "--target", target}, &bytes.Buffer{}, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	return target
}

func recorded(t *testing.T, log string) (string, []string) {
	t.Helper()
	data, err := os.ReadFile(log)
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimRight(string(data), "\n"), "\n")
	return lines[0], lines[1:]
}

func TestRunStarWithConfigPrecedenceAndPassthrough(t *testing.T) {
	target := installedTarget(t)
	if err := SaveConfig(target, Config{TestCommand: "go test ./...", LintCommand: "go vet ./...", Memory: Memory{Project: "demo"}}); err != nil {
		t.Fatal(err)
	}
	_, log := fakeConductor(t, "exit 0")
	var stdout bytes.Buffer
	args := []string{"cancel", "a", "pending", "order", "--target", target, "--lint-command", "make lint", "--", "--log-file", "auto"}
	if err := Run(append([]string{"run"}, args...), &stdout, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	dir, got := recorded(t, log)
	resolved, _ := filepath.EvalSymlinks(target)
	if dir != target && dir != resolved {
		t.Fatalf("conductor ran in %q, want %q", dir, target)
	}
	want := []string{"run", "workflows/conductor/layered-star.yaml", "--web", "--workspace-instructions",
		"--input", "request=cancel a pending order", "--input", "test_command=go test ./...",
		"--input", "lint_command=make lint", "--input", "memory_project=demo", "--log-file", "auto"}
	if strings.Join(got, "|") != strings.Join(want, "|") {
		t.Fatalf("args = %q\nwant  %q", got, want)
	}
	if run, err := readLastRun(target); err != nil || run.Flow != FlowStar {
		t.Fatalf("last run = %+v, %v", run, err)
	}
}

func TestRunWithoutRequestLetsTheObjectiveGateAsk(t *testing.T) {
	target := installedTarget(t)
	_, log := fakeConductor(t, "exit 0")
	if err := Run([]string{"run", "--target", target, "--no-web"}, &bytes.Buffer{}, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	_, got := recorded(t, log)
	for _, arg := range got {
		if strings.HasPrefix(arg, "request=") || arg == "--web" {
			t.Fatalf("unexpected %q in %q", arg, got)
		}
	}
}

func TestRunLinearAndSave(t *testing.T) {
	target := installedTarget(t)
	_, log := fakeConductor(t, "exit 0")
	if err := Run([]string{"run", "--target", target, "--flow", "linear", "--save", "--security-command", "make audit-ci", "fix", "it"}, &bytes.Buffer{}, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	_, got := recorded(t, log)
	if got[1] != "workflows/conductor/layered-tdd.yaml" {
		t.Fatalf("args = %q", got)
	}
	config, err := LoadConfig(target)
	if err != nil || config.SecurityCommand != "make audit-ci" {
		t.Fatalf("saved config = %+v, %v", config, err)
	}
}

func TestRunRejectsBeforeLaunching(t *testing.T) {
	target := installedTarget(t)
	_, log := fakeConductor(t, "exit 0")
	cases := map[string][]string{
		"needs a request":               {"run", "--target", target, "--unattended"},
		"linear flow needs":             {"run", "--target", target, "--flow", "linear"},
		"unknown flow":                  {"run", "--target", target, "--flow", "ring", "x"},
		"not found under .github/plans": {"run", "--target", target, "--plan", "missing", "x"},
	}
	for want, args := range cases {
		err := Run(args, &bytes.Buffer{}, &bytes.Buffer{})
		if err == nil || !strings.Contains(err.Error(), want) {
			t.Fatalf("%v: error = %v, want %q", args, err, want)
		}
	}
	if _, err := os.Stat(log); !os.IsNotExist(err) {
		t.Fatal("conductor must not run when the command is rejected")
	}

	previous := lookPath
	lookPath = func(string) (string, error) { return "", errors.New("missing") }
	defer func() { lookPath = previous }()
	if err := Run([]string{"run", "--target", target, "x"}, &bytes.Buffer{}, &bytes.Buffer{}); err == nil || !strings.Contains(err.Error(), "install Microsoft Conductor") {
		t.Fatalf("missing conductor error = %v", err)
	}
	lookPath = previous
	empty := t.TempDir()
	fakeConductor(t, "exit 0")
	if err := Run([]string{"run", "--target", empty, "x"}, &bytes.Buffer{}, &bytes.Buffer{}); err == nil || !strings.Contains(err.Error(), "ltdd install") {
		t.Fatalf("missing workflow error = %v", err)
	}
}

func TestUnattendedNeedsHumanStopPointsToResume(t *testing.T) {
	target := installedTarget(t)
	ledger := `mkdir -p .github/plans/smoke && printf '{"snapshot":{"status":"needs-human"}}' > .github/plans/smoke/star-state.json; exit 1`
	_, log := fakeConductor(t, ledger)
	var stdout bytes.Buffer
	err := Run([]string{"run", "--target", target, "--unattended", "cancel", "orders"}, &stdout, &bytes.Buffer{})
	if !errors.Is(err, ErrNeedsHuman) {
		t.Fatalf("error = %v, want ErrNeedsHuman", err)
	}
	if !strings.Contains(stdout.String(), "ltdd resume") || !strings.Contains(stdout.String(), ".github/plans/smoke/star-state.json") {
		t.Fatalf("stdout = %q", stdout.String())
	}
	_, got := recorded(t, log)
	if !strings.Contains(strings.Join(got, " "), "--skip-gates") {
		t.Fatalf("unattended run must pass --skip-gates: %q", got)
	}

	_, log = fakeConductor(t, "exit 0")
	if err := Run([]string{"resume", "--target", target}, &bytes.Buffer{}, &bytes.Buffer{}); err != nil {
		t.Fatal(err)
	}
	_, got = recorded(t, log)
	if strings.Join(got, " ") != "resume workflows/conductor/layered-star.yaml --web" {
		t.Fatalf("resume args = %q", got)
	}
}
