package validator

import (
	"os"
	"testing"
)

func TestAgentRepro(t *testing.T) {
	origArgs := os.Args
	defer func() { os.Args = origArgs }()

	os.Args = []string{"prog", "__complete", "x"}

	var capturedArgs []string

	cmd := &Command{
		TraverseChildren: true,
		Run:              func(cmd *Command, args []string) {},
		ValidArgsFunction: func(cmd *Command, args []string, toComplete string) ([]Completion, ShellCompDirective) {
			capturedArgs = append([]string(nil), os.Args...)
			return nil, ShellCompDirectiveDefault
		},
	}

	if err := cmd.Execute(); err != nil {
		t.Fatalf("Execute returned error: %v", err)
	}

	for _, a := range capturedArgs {
		if a == "--" {
			t.Fatalf("os.Args was modified to insert \"--\": %v", capturedArgs)
		}
	}
}