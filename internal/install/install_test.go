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
	assertContains(t, manifestPath(target), `"provider": "copilot"`)
}

func TestClaudeInstallAndSyncKeepProvider(t *testing.T) {
	target := t.TempDir()
	if _, err := Run(Options{Target: target, Provider: ProviderClaude}); err != nil {
		t.Fatal(err)
	}
	workflow := filepath.Join(target, "workflows", "conductor", "layered-tdd.yaml")
	assertContains(t, workflow, "name: claude-agent-sdk")
	assertContains(t, workflow, "auth_mode: subscription")
	assertContains(t, workflow, "native_tools: claude_code")
	assertContains(t, workflow, "model: claude-haiku-5-5")
	assertContains(t, workflow, "model: claude-sonnet-5-5")
	claudeData, err := os.ReadFile(workflow)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(claudeData), "reasoning:") || strings.Contains(string(claudeData), "default_reasoning_effort:") {
		t.Fatal("Claude Agent SDK variant must omit unsupported reasoning effort settings")
	}
	assertContains(t, manifestPath(target), `"provider": "claude-agent-sdk"`)
	result, err := Run(Options{Target: target, Mode: ModeSync})
	if err != nil {
		t.Fatal(err)
	}
	if len(result.Updated) != 0 || len(result.Preserved) != 0 {
		t.Fatalf("sync changed clean Claude install: %+v", result)
	}
	assertContains(t, workflow, "name: claude-agent-sdk")
}

func TestProviderChangeRequiresForce(t *testing.T) {
	target := t.TempDir()
	if _, err := Run(Options{Target: target}); err != nil {
		t.Fatal(err)
	}
	if _, err := Run(Options{Target: target, Mode: ModeSync, Provider: ProviderClaude}); err == nil || !strings.Contains(err.Error(), "requires --force") {
		t.Fatalf("expected explicit replacement boundary, got %v", err)
	}
	assertContains(t, filepath.Join(target, "workflows", "conductor", "layered-tdd.yaml"), "provider: copilot")
}

func TestLegacyManifestKeepsCopilotProvider(t *testing.T) {
	target := t.TempDir()
	if _, err := Run(Options{Target: target}); err != nil {
		t.Fatal(err)
	}
	previous, err := readManifest(target)
	if err != nil {
		t.Fatal(err)
	}
	previous.Provider = ""
	if err := writeManifest(target, previous); err != nil {
		t.Fatal(err)
	}
	if _, err := Run(Options{Target: target, Mode: ModeSync, Provider: ProviderClaude}); err == nil || !strings.Contains(err.Error(), "requires --force") {
		t.Fatalf("expected legacy Copilot install to resist provider switch, got %v", err)
	}
	if _, err := Run(Options{Target: target, Mode: ModeSync}); err != nil {
		t.Fatal(err)
	}
	assertContains(t, manifestPath(target), `"provider": "copilot"`)
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
