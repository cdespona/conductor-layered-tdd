package cli

import (
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	installer "github.com/cdespona/conductor-layered-tdd/internal/install"
)

var Version = "dev"

func Run(args []string, stdout, stderr io.Writer) error {
	if len(args) == 0 {
		printUsage(stdout)
		return nil
	}
	switch args[0] {
	case "install":
		return runInstall(args[1:], installer.ModeInstall, stdout, stderr)
	case "sync":
		return runInstall(args[1:], installer.ModeSync, stdout, stderr)
	case "validate":
		return runValidate(args[1:], stdout, stderr)
	case "version", "--version", "-v":
		_, err := fmt.Fprintf(stdout, "ltdd %s\n", Version)
		return err
	case "help", "--help", "-h":
		printUsage(stdout)
		return nil
	default:
		return fmt.Errorf("unknown command %q", args[0])
	}
}

func runInstall(args []string, mode installer.Mode, stdout, stderr io.Writer) error {
	flags := flag.NewFlagSet(string(mode), flag.ContinueOnError)
	flags.SetOutput(stderr)
	target := flags.String("target", ".", "repository to install into")
	memory := flags.Bool("memory-skills", false, "include Markdown memory skills")
	force := flags.Bool("force", false, "replace locally edited managed files")
	if err := flags.Parse(args); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return fmt.Errorf("unexpected arguments: %s", strings.Join(flags.Args(), " "))
	}
	result, err := installer.Run(installer.Options{
		Target:              *target,
		Mode:                mode,
		IncludeMemorySkills: *memory,
		Force:               *force,
	})
	if err != nil {
		return err
	}
	fmt.Fprintf(stdout, "LTDD %s complete: %d created, %d updated, %d unchanged, %d preserved\n", mode, len(result.Created), len(result.Updated), len(result.Unchanged), len(result.Preserved))
	for _, path := range result.Preserved {
		fmt.Fprintf(stdout, "[preserved] %s\n", path)
	}
	if len(result.Preserved) > 0 {
		fmt.Fprintln(stdout, "Use --force only when you intentionally want bundled files to replace local edits.")
	}
	return nil
}

func runValidate(args []string, stdout, stderr io.Writer) error {
	flags := flag.NewFlagSet("validate", flag.ContinueOnError)
	flags.SetOutput(stderr)
	target := flags.String("target", ".", "installed repository to validate")
	if err := flags.Parse(args); err != nil {
		return err
	}
	absTarget, err := filepath.Abs(*target)
	if err != nil {
		return fmt.Errorf("resolve validation target: %w", err)
	}
	workflow := filepath.Join(absTarget, "workflows", "conductor", "layered-tdd.yaml")
	data, err := os.ReadFile(workflow)
	if err != nil {
		return fmt.Errorf("read installed workflow %q: %w", workflow, err)
	}
	for _, marker := range []string{
		"entry_point: preflight_tests",
		"mode: explicit",
		"layer_selection_gate.output.additional_input.selected_layer",
		"layer_map_revision_gate.output.additional_input.selected_layer",
	} {
		if !strings.Contains(string(data), marker) {
			return fmt.Errorf("workflow validation failed: missing %q", marker)
		}
	}

	conductor, err := exec.LookPath("conductor")
	if err != nil {
		fmt.Fprintln(stdout, "LTDD structure: valid")
		fmt.Fprintln(stdout, "Conductor CLI not found; skipped schema validation.")
		return nil
	}
	command := exec.Command(conductor, "validate", workflow)
	command.Dir = absTarget
	command.Stdout = stdout
	command.Stderr = stderr
	if err := command.Run(); err != nil {
		return fmt.Errorf("conductor validate failed: %w", err)
	}
	fmt.Fprintln(stdout, "LTDD validation: passed")
	return nil
}

func printUsage(output io.Writer) {
	fmt.Fprintln(output, `ltdd installs and maintains the Conductor layered-TDD workflow.

Usage:
  ltdd install [--target PATH] [--memory-skills] [--force]
  ltdd sync [--target PATH] [--memory-skills] [--force]
  ltdd validate [--target PATH]
  ltdd version`)
}
