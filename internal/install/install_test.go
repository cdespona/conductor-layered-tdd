package install

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestInstallCopiesWorkflowAndCoreSkills(t *testing.T) {
	target := t.TempDir()
	result, err := Run(Options{Target: target, Mode: ModeInstall})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Created) == 0 {
		t.Fatal("expected files to be created")
	}
	assertContains(t, filepath.Join(target, "workflows", "conductor", "layered-tdd.yaml"), "entry_point: preflight_tests")
	assertContains(t, filepath.Join(target, ".github", "skills", "conductor-tdd", "SKILL.md"), "Test-Driven Development")
	if _, err := os.Stat(filepath.Join(target, ".github", "skills", "conductor-memory-recall", "SKILL.md")); !os.IsNotExist(err) {
		t.Fatalf("memory skill should be opt-in; stat error = %v", err)
	}
	assertContains(t, filepath.Join(target, ".gitignore"), ".ltdd/")
	assertContains(t, manifestPath(target), "workflows/conductor/layered-tdd.yaml")
}

func TestInstallCanIncludeAllMemorySkills(t *testing.T) {
	target := t.TempDir()
	if _, err := Run(Options{Target: target, IncludeMemorySkills: true}); err != nil {
		t.Fatal(err)
	}
	for _, skill := range memorySkills {
		path := filepath.Join(target, ".github", "skills", "conductor-"+skill, "SKILL.md")
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("expected memory skill %q: %v", skill, err)
		}
	}
}

func TestInstallPreservesExistingEdits(t *testing.T) {
	target := t.TempDir()
	workflow := filepath.Join(target, "workflows", "conductor", "layered-tdd.yaml")
	if err := os.MkdirAll(filepath.Dir(workflow), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(workflow, []byte("local edit\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	result, err := Run(Options{Target: target})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Preserved) != 1 || result.Preserved[0] != "workflows/conductor/layered-tdd.yaml" {
		t.Fatalf("preserved = %v", result.Preserved)
	}
	assertContains(t, workflow, "local edit")
}

func TestSyncUpdatesManagedFilesButPreservesUserChanges(t *testing.T) {
	target := t.TempDir()
	if _, err := Run(Options{Target: target}); err != nil {
		t.Fatal(err)
	}
	workflow := filepath.Join(target, "workflows", "conductor", "layered-tdd.yaml")
	data, err := os.ReadFile(workflow)
	if err != nil {
		t.Fatal(err)
	}
	previous, err := readManifest(target)
	if err != nil {
		t.Fatal(err)
	}
	previous.Files["workflows/conductor/layered-tdd.yaml"] = hash([]byte("old managed content\n"))
	if err := writeManifest(target, previous); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(workflow, []byte("old managed content\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	result, err := Run(Options{Target: target, Mode: ModeSync})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Updated) != 1 || result.Updated[0] != "workflows/conductor/layered-tdd.yaml" {
		t.Fatalf("updated = %v", result.Updated)
	}
	got, err := os.ReadFile(workflow)
	if err != nil {
		t.Fatal(err)
	}
	if string(got) != string(data) {
		t.Fatal("sync did not restore the current bundled workflow")
	}

	if err := os.WriteFile(workflow, []byte("new user edit\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	result, err = Run(Options{Target: target, Mode: ModeSync})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Preserved) != 1 || result.Preserved[0] != "workflows/conductor/layered-tdd.yaml" {
		t.Fatalf("preserved after user edit = %v", result.Preserved)
	}
}

func TestForceReplacesExistingEdits(t *testing.T) {
	target := t.TempDir()
	workflow := filepath.Join(target, "workflows", "conductor", "layered-tdd.yaml")
	if err := os.MkdirAll(filepath.Dir(workflow), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(workflow, []byte("local edit\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	result, err := Run(Options{Target: target, Force: true})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Updated) != 1 {
		t.Fatalf("updated = %v", result.Updated)
	}
	assertContains(t, workflow, "entry_point: preflight_tests")
}

func assertContains(t *testing.T, path, marker string) {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read %q: %v", path, err)
	}
	if !strings.Contains(string(data), marker) {
		t.Fatalf("%q does not contain %q", path, marker)
	}
}
