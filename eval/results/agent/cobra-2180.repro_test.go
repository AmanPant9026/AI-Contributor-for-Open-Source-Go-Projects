package validator

import (
	"bytes"
	"strings"
	"testing"
)

func TestAgentRepro(t *testing.T) {
	// A plugin-style command that has a Version set.
	root := &Command{
		Use:     "kubectl-plugin",
		Version: "1.0.0",
		Run:     func(cmd *Command, args []string) {},
	}

	buf := new(bytes.Buffer)
	root.SetOutput(buf)
	root.SetArgs([]string{"--version"})

	if err := root.Execute(); err != nil {
		t.Fatalf("unexpected error executing command: %v", err)
	}

	out := buf.String()
	// The version output should contain the command name and version.
	if !strings.Contains(out, "1.0.0") {
		t.Fatalf("expected version output to contain the version %q, got: %q", "1.0.0", out)
	}
	if !strings.Contains(out, "kubectl-plugin") {
		t.Fatalf("expected version output to contain the command name %q, got: %q", "kubectl-plugin", out)
	}
}