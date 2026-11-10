package cli

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// Flow names accepted by `ltdd run --flow`.
const (
	FlowStar   = "star"
	FlowLinear = "linear"
)

var flowWorkflows = map[string]string{
	FlowStar:   "workflows/conductor/layered-star.yaml",
	FlowLinear: "workflows/conductor/layered-tdd.yaml",
}

// ErrNeedsHuman reports an unattended star run that stopped at a question only a human may answer.
var ErrNeedsHuman = errors.New("run stopped: needs human; answer the pending confirmation with `ltdd resume`")

// lookPath, makeCommand and now are replaced in tests.
var (
	lookPath    = exec.LookPath
	makeCommand = exec.Command
	now         = time.Now
)

// Memory holds the Markdown memory settings of `.ltdd/config.json`.
type Memory struct {
	Vault     string `json:"vault,omitempty"`
	Namespace string `json:"namespace,omitempty"`
	Project   string `json:"project,omitempty"`
}

// Config is the per-repository `.ltdd/config.json`. Precedence: flag > config > workflow default.
type Config struct {
	TestCommand     string `json:"test_command,omitempty"`
	LintCommand     string `json:"lint_command,omitempty"`
	SecurityCommand string `json:"security_command,omitempty"`
	Memory          Memory `json:"memory,omitempty"`
	GraphifyScope   string `json:"graphify_scope,omitempty"`
}

type lastRun struct {
	Flow       string `json:"flow"`
	Workflow   string `json:"workflow"`
	StartedAt  string `json:"started_at"`
	Unattended bool   `json:"unattended"`
	Plan       string `json:"plan,omitempty"`
}

func configPath(target string) string { return filepath.Join(target, ".ltdd", "config.json") }

func lastRunPath(target string) string { return filepath.Join(target, ".ltdd", "last-run.json") }

// LoadConfig reads `.ltdd/config.json`; a missing file is an empty config.
func LoadConfig(target string) (Config, error) {
	var config Config
	data, err := os.ReadFile(configPath(target))
	if os.IsNotExist(err) {
		return config, nil
	}
	if err != nil {
		return config, fmt.Errorf("read %s: %w", configPath(target), err)
	}
	if err := json.Unmarshal(data, &config); err != nil {
		return config, fmt.Errorf("decode %s: %w", configPath(target), err)
	}
	return config, nil
}

// SaveConfig writes `.ltdd/config.json`.
func SaveConfig(target string, config Config) error {
	data, err := json.MarshalIndent(config, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(configPath(target)), 0o755); err != nil {
		return err
	}
	return os.WriteFile(configPath(target), append(data, '\n'), 0o644)
}

type stringFlag struct {
	value string
	set   bool
}

func (f *stringFlag) String() string { return f.value }

func (f *stringFlag) Set(value string) error {
	f.value, f.set = value, true
	return nil
}

// pick applies flag > config precedence; an empty result leaves the workflow default.
func pick(flagValue *stringFlag, configValue string) string {
	if flagValue.set {
		return flagValue.value
	}
	return configValue
}

func splitPassthrough(args []string) ([]string, []string) {
	for index, arg := range args {
		if arg == "--" {
			return args[:index], args[index+1:]
		}
	}
	return args, nil
}

// interleaved lets flags follow positional request words (`ltdd run fix the bug --flow linear`).
func parseInterleaved(flags *flag.FlagSet, args []string) ([]string, error) {
	positional := []string{}
	for {
		if err := flags.Parse(args); err != nil {
			return nil, err
		}
		args = flags.Args()
		if len(args) == 0 {
			return positional, nil
		}
		positional = append(positional, args[0])
		args = args[1:]
	}
}

func runRun(args []string, stdout, stderr io.Writer) error {
	own, passthrough := splitPassthrough(args)
	flags := flag.NewFlagSet("run", flag.ContinueOnError)
	flags.SetOutput(stderr)
	target := flags.String("target", ".", "repository with the installed workflows")
	flowFlag := &stringFlag{}
	flags.Var(flowFlag, "flow", "star (default) or linear")
	noWeb := flags.Bool("no-web", false, "do not start the Conductor web dashboard")
	unattended := flags.Bool("unattended", false, "run with --skip-gates; requires a request")
	plan := flags.String("plan", "", "resume an existing .github/plans/<slug> (input resume_path)")
	save := flags.Bool("save", false, "save the flags passed to .ltdd/config.json")
	testCommand, lintCommand, securityCommand := &stringFlag{}, &stringFlag{}, &stringFlag{}
	vault, namespace, project, scope := &stringFlag{}, &stringFlag{}, &stringFlag{}, &stringFlag{}
	flags.Var(testCommand, "test-command", "full test suite command")
	flags.Var(lintCommand, "lint-command", "lint/static check command")
	flags.Var(securityCommand, "security-command", "dependency/CVE check command")
	flags.Var(vault, "memory-vault", "absolute path to the Markdown memory vault")
	flags.Var(namespace, "memory-namespace", "Markdown memory namespace")
	flags.Var(project, "memory-project", "Markdown memory project slug")
	flags.Var(scope, "graphify-scope", "relative code directory for Graphify")
	words, err := parseInterleaved(flags, own)
	if err != nil {
		return err
	}
	request := strings.TrimSpace(strings.Join(words, " "))

	absTarget, err := filepath.Abs(*target)
	if err != nil {
		return fmt.Errorf("resolve target: %w", err)
	}
	config, err := LoadConfig(absTarget)
	if err != nil {
		return err
	}
	flow := FlowStar
	if flowFlag.set {
		flow = flowFlag.value
	}
	workflow, ok := flowWorkflows[flow]
	if !ok {
		return fmt.Errorf("unknown flow %q (choose star or linear)", flow)
	}
	if *unattended && request == "" {
		return errors.New("--unattended needs a request: without one the objective gate would loop until max_iterations")
	}
	if flow == FlowLinear && request == "" && *plan == "" {
		return errors.New("the linear flow needs a request: ltdd run --flow linear \"describe the change\"")
	}
	conductor, err := requireConductor()
	if err != nil {
		return err
	}
	if err := requireWorkflow(absTarget, workflow); err != nil {
		return err
	}

	inputs := [][2]string{}
	add := func(name, value string) {
		if value != "" {
			inputs = append(inputs, [2]string{name, value})
		}
	}
	add("request", request)
	add("test_command", pick(testCommand, config.TestCommand))
	add("lint_command", pick(lintCommand, config.LintCommand))
	add("security_command", pick(securityCommand, config.SecurityCommand))
	add("memory_vault", pick(vault, config.Memory.Vault))
	add("memory_namespace", pick(namespace, config.Memory.Namespace))
	add("memory_project", pick(project, config.Memory.Project))
	add("graphify_scope", pick(scope, config.GraphifyScope))
	if *plan != "" {
		planDir := filepath.ToSlash(filepath.Join(".github", "plans", *plan))
		if info, err := os.Stat(filepath.Join(absTarget, planDir)); err != nil || !info.IsDir() {
			return fmt.Errorf("plan %q not found under .github/plans/", *plan)
		}
		add("resume_path", planDir)
	}

	if *save {
		for _, item := range []struct {
			flag  *stringFlag
			field *string
		}{
			{testCommand, &config.TestCommand}, {lintCommand, &config.LintCommand},
			{securityCommand, &config.SecurityCommand}, {vault, &config.Memory.Vault},
			{namespace, &config.Memory.Namespace}, {project, &config.Memory.Project},
			{scope, &config.GraphifyScope},
		} {
			if item.flag.set {
				*item.field = item.flag.value
			}
		}
		if err := SaveConfig(absTarget, config); err != nil {
			return fmt.Errorf("save config: %w", err)
		}
		fmt.Fprintf(stdout, "Saved %s\n", configPath(absTarget))
	}

	commandArgs := []string{"run", workflow}
	if !*noWeb {
		commandArgs = append(commandArgs, "--web")
	}
	commandArgs = append(commandArgs, "--workspace-instructions")
	if *unattended {
		commandArgs = append(commandArgs, "--skip-gates")
	}
	for _, input := range inputs {
		commandArgs = append(commandArgs, "--input", input[0]+"="+input[1])
	}
	commandArgs = append(commandArgs, passthrough...)

	started := now()
	if err := writeLastRun(absTarget, lastRun{Flow: flow, Workflow: workflow, StartedAt: started.UTC().Format(time.RFC3339),
		Unattended: *unattended, Plan: *plan}); err != nil {
		return err
	}
	return launch(conductor, commandArgs, absTarget, flow, stdout, stderr)
}

func runResume(args []string, stdout, stderr io.Writer) error {
	own, passthrough := splitPassthrough(args)
	flags := flag.NewFlagSet("resume", flag.ContinueOnError)
	flags.SetOutput(stderr)
	target := flags.String("target", ".", "repository with the installed workflows")
	flowFlag := &stringFlag{}
	flags.Var(flowFlag, "flow", "star or linear (defaults to the last run)")
	noWeb := flags.Bool("no-web", false, "do not start the Conductor web dashboard")
	if err := flags.Parse(own); err != nil {
		return err
	}
	if flags.NArg() != 0 {
		return fmt.Errorf("unexpected arguments: %s", strings.Join(flags.Args(), " "))
	}
	absTarget, err := filepath.Abs(*target)
	if err != nil {
		return fmt.Errorf("resolve target: %w", err)
	}
	flow := FlowStar
	if previous, err := readLastRun(absTarget); err == nil && previous.Flow != "" {
		flow = previous.Flow
	}
	if flowFlag.set {
		flow = flowFlag.value
	}
	workflow, ok := flowWorkflows[flow]
	if !ok {
		return fmt.Errorf("unknown flow %q (choose star or linear)", flow)
	}
	conductor, err := requireConductor()
	if err != nil {
		return err
	}
	if err := requireWorkflow(absTarget, workflow); err != nil {
		return err
	}
	commandArgs := []string{"resume", workflow}
	if !*noWeb {
		commandArgs = append(commandArgs, "--web")
	}
	commandArgs = append(commandArgs, passthrough...)
	return launch(conductor, commandArgs, absTarget, flow, stdout, stderr)
}

func requireConductor() (string, error) {
	conductor, err := lookPath("conductor")
	if err != nil {
		return "", errors.New("conductor CLI not found on PATH: install Microsoft Conductor (conductor-cli) so that `conductor --version` works, then retry")
	}
	return conductor, nil
}

func requireWorkflow(target, workflow string) error {
	if _, err := os.Stat(filepath.Join(target, filepath.FromSlash(workflow))); err != nil {
		return fmt.Errorf("workflow %s is not installed in %s: run `ltdd install` (or `ltdd sync` to update an existing install)", workflow, target)
	}
	return nil
}

func launch(conductor string, args []string, target, flow string, stdout, stderr io.Writer) error {
	before := ledgerTimes(target)
	command := makeCommand(conductor, args...)
	command.Dir = target
	command.Stdin = os.Stdin
	command.Stdout = stdout
	command.Stderr = stderr
	runErr := command.Run()
	if flow == FlowStar {
		if ledger := needsHuman(target, before); ledger != "" {
			fmt.Fprintf(stdout, "\nThe star run stopped with status needs-human (ledger %s).\nAnswer the pending confirmation with:\n  ltdd resume\n", ledger)
			return ErrNeedsHuman
		}
	}
	if runErr != nil {
		return fmt.Errorf("conductor %s failed: %w", args[0], runErr)
	}
	return nil
}

type ledgerDocument struct {
	Snapshot struct {
		Status string `json:"status"`
	} `json:"snapshot"`
}

func ledgerPaths(target string) []string {
	paths, _ := filepath.Glob(filepath.Join(target, ".github", "plans", "*", "star-state.json"))
	pending := filepath.Join(target, ".github", "plans", ".star-pending", "star-state.json")
	if _, err := os.Stat(pending); err == nil {
		paths = append(paths, pending)
	}
	return paths
}

func ledgerTimes(target string) map[string]time.Time {
	times := map[string]time.Time{}
	for _, path := range ledgerPaths(target) {
		if info, err := os.Stat(path); err == nil {
			times[path] = info.ModTime()
		}
	}
	return times
}

// needsHuman returns the most recent star ledger written by this launch whose status is needs-human.
func needsHuman(target string, before map[string]time.Time) string {
	type candidate struct {
		path string
		mod  time.Time
	}
	candidates := []candidate{}
	for _, path := range ledgerPaths(target) {
		info, err := os.Stat(path)
		if err != nil {
			continue
		}
		if previous, ok := before[path]; ok && previous.Equal(info.ModTime()) {
			continue
		}
		candidates = append(candidates, candidate{path, info.ModTime()})
	}
	sort.Slice(candidates, func(i, j int) bool { return candidates[i].mod.After(candidates[j].mod) })
	for _, item := range candidates {
		data, err := os.ReadFile(item.path)
		if err != nil {
			continue
		}
		var document ledgerDocument
		if json.Unmarshal(data, &document) == nil && document.Snapshot.Status == "needs-human" {
			if relative, err := filepath.Rel(target, item.path); err == nil {
				return filepath.ToSlash(relative)
			}
			return item.path
		}
	}
	return ""
}

func writeLastRun(target string, run lastRun) error {
	data, err := json.MarshalIndent(run, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(lastRunPath(target)), 0o755); err != nil {
		return err
	}
	return os.WriteFile(lastRunPath(target), append(data, '\n'), 0o644)
}

func readLastRun(target string) (lastRun, error) {
	var run lastRun
	data, err := os.ReadFile(lastRunPath(target))
	if err != nil {
		return run, err
	}
	return run, json.Unmarshal(data, &run)
}
