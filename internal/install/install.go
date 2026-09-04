package install

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/cdespona/conductor-layered-tdd/bundle"
)

const manifestVersion = 1

var coreSkills = []string{
	"tdd",
	"test-type-classification",
	"cognitive-doc-design",
	"work-unit-commits",
	"comment-writer",
}

var memorySkills = []string{
	"memory-recall",
	"memory-capture",
	"memory-consolidate",
}

type Mode string

const (
	ModeInstall Mode = "install"
	ModeSync    Mode = "sync"
)

type Options struct {
	Target              string
	Mode                Mode
	IncludeMemorySkills bool
	Force               bool
}

type Result struct {
	Created   []string
	Updated   []string
	Unchanged []string
	Preserved []string
}

type manifest struct {
	Version int               `json:"version"`
	Files   map[string]string `json:"files"`
}

type bundledFile struct {
	Source string
	Target string
	Data   []byte
}

func Run(opts Options) (Result, error) {
	if opts.Mode == "" {
		opts.Mode = ModeInstall
	}
	if opts.Mode != ModeInstall && opts.Mode != ModeSync {
		return Result{}, fmt.Errorf("unsupported install mode %q", opts.Mode)
	}
	target, err := resolveTarget(opts.Target)
	if err != nil {
		return Result{}, err
	}

	previous, err := readManifest(target)
	if err != nil {
		return Result{}, err
	}
	files, err := bundledFiles(opts.IncludeMemorySkills)
	if err != nil {
		return Result{}, err
	}

	next := manifest{Version: manifestVersion, Files: map[string]string{}}
	result := Result{}
	for _, file := range files {
		destination := filepath.Join(target, filepath.FromSlash(file.Target))
		wantedHash := hash(file.Data)
		current, readErr := os.ReadFile(destination)
		switch {
		case os.IsNotExist(readErr):
			if err := writeAtomic(destination, file.Data, 0o644); err != nil {
				return Result{}, err
			}
			result.Created = append(result.Created, file.Target)
			next.Files[file.Target] = wantedHash
		case readErr != nil:
			return Result{}, fmt.Errorf("read target file %q: %w", destination, readErr)
		case hash(current) == wantedHash:
			result.Unchanged = append(result.Unchanged, file.Target)
			next.Files[file.Target] = wantedHash
		case opts.Force:
			if err := writeAtomic(destination, file.Data, 0o644); err != nil {
				return Result{}, err
			}
			result.Updated = append(result.Updated, file.Target)
			next.Files[file.Target] = wantedHash
		case opts.Mode == ModeSync && previous.Files[file.Target] != "" && hash(current) == previous.Files[file.Target]:
			if err := writeAtomic(destination, file.Data, 0o644); err != nil {
				return Result{}, err
			}
			result.Updated = append(result.Updated, file.Target)
			next.Files[file.Target] = wantedHash
		default:
			result.Preserved = append(result.Preserved, file.Target)
		}
	}

	if err := ensureGitignore(target); err != nil {
		return Result{}, err
	}
	if err := writeManifest(target, next); err != nil {
		return Result{}, err
	}
	return result, nil
}

func bundledFiles(includeMemory bool) ([]bundledFile, error) {
	skills := append([]string{}, coreSkills...)
	if includeMemory {
		skills = append(skills, memorySkills...)
	}
	allowedSkills := map[string]bool{}
	for _, skill := range skills {
		allowedSkills[skill] = true
	}

	files := []bundledFile{}
	err := fs.WalkDir(bundle.FS, ".", func(path string, entry fs.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() || path == "embed.go" {
			return nil
		}

		target := ""
		switch {
		case strings.HasPrefix(path, "workflows/"):
			target = path
		case strings.HasPrefix(path, "skills/"):
			parts := strings.Split(path, "/")
			if len(parts) < 3 || !allowedSkills[parts[1]] {
				return nil
			}
			target = filepath.ToSlash(filepath.Join(".github", "skills", "conductor-"+parts[1], filepath.Join(parts[2:]...)))
		default:
			return nil
		}

		data, err := bundle.FS.ReadFile(path)
		if err != nil {
			return fmt.Errorf("read bundled file %q: %w", path, err)
		}
		files = append(files, bundledFile{Source: path, Target: target, Data: data})
		return nil
	})
	if err != nil {
		return nil, fmt.Errorf("walk embedded LTDD bundle: %w", err)
	}
	sort.Slice(files, func(i, j int) bool { return files[i].Target < files[j].Target })
	return files, nil
}

func resolveTarget(target string) (string, error) {
	if strings.TrimSpace(target) == "" {
		target = "."
	}
	abs, err := filepath.Abs(target)
	if err != nil {
		return "", fmt.Errorf("resolve target %q: %w", target, err)
	}
	info, err := os.Stat(abs)
	if err != nil {
		return "", fmt.Errorf("inspect target %q: %w", abs, err)
	}
	if !info.IsDir() {
		return "", fmt.Errorf("target %q is not a directory", abs)
	}
	return abs, nil
}

func manifestPath(target string) string {
	return filepath.Join(target, ".ltdd", "manifest.json")
}

func readManifest(target string) (manifest, error) {
	data, err := os.ReadFile(manifestPath(target))
	if os.IsNotExist(err) {
		return manifest{Version: manifestVersion, Files: map[string]string{}}, nil
	}
	if err != nil {
		return manifest{}, fmt.Errorf("read LTDD manifest: %w", err)
	}
	var value manifest
	if err := json.Unmarshal(data, &value); err != nil {
		return manifest{}, fmt.Errorf("decode LTDD manifest: %w", err)
	}
	if value.Files == nil {
		value.Files = map[string]string{}
	}
	return value, nil
}

func writeManifest(target string, value manifest) error {
	data, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return fmt.Errorf("encode LTDD manifest: %w", err)
	}
	data = append(data, '\n')
	if err := writeAtomic(manifestPath(target), data, 0o644); err != nil {
		return fmt.Errorf("write LTDD manifest: %w", err)
	}
	return nil
}

func writeAtomic(path string, data []byte, mode fs.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return fmt.Errorf("create parent directory for %q: %w", path, err)
	}
	temporary, err := os.CreateTemp(filepath.Dir(path), ".ltdd-*")
	if err != nil {
		return fmt.Errorf("create temporary file for %q: %w", path, err)
	}
	temporaryPath := temporary.Name()
	defer os.Remove(temporaryPath)
	if _, err := temporary.Write(data); err != nil {
		temporary.Close()
		return fmt.Errorf("write temporary file for %q: %w", path, err)
	}
	if err := temporary.Chmod(mode); err != nil {
		temporary.Close()
		return fmt.Errorf("set mode for %q: %w", path, err)
	}
	if err := temporary.Close(); err != nil {
		return fmt.Errorf("close temporary file for %q: %w", path, err)
	}
	if err := os.Rename(temporaryPath, path); err != nil {
		return fmt.Errorf("replace %q: %w", path, err)
	}
	return nil
}

func ensureGitignore(target string) error {
	path := filepath.Join(target, ".gitignore")
	data, err := os.ReadFile(path)
	if err != nil && !os.IsNotExist(err) {
		return fmt.Errorf("read %q: %w", path, err)
	}
	content := string(data)
	existing := map[string]bool{}
	for _, line := range strings.Split(content, "\n") {
		existing[strings.TrimSpace(line)] = true
	}
	entries := []string{
		"workflows/conductor/",
		".github/plans/",
		".github/skills/conductor-*/",
		"graphify-out/",
		".ltdd/",
	}
	missing := []string{}
	for _, entry := range entries {
		if !existing[entry] {
			missing = append(missing, entry)
		}
	}
	if len(missing) == 0 {
		return nil
	}
	var output strings.Builder
	output.WriteString(content)
	if content != "" && !strings.HasSuffix(content, "\n") {
		output.WriteByte('\n')
	}
	if content != "" {
		output.WriteByte('\n')
	}
	output.WriteString("# LTDD generated workflow and local state\n")
	for _, entry := range missing {
		output.WriteString(entry)
		output.WriteByte('\n')
	}
	return writeAtomic(path, []byte(output.String()), 0o644)
}

func hash(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}
