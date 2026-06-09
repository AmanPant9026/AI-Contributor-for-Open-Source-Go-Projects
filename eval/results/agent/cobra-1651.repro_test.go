package cobra

import (
	"bytes"
	"strings"
	"testing"
)

func TestAgentRepro(t *testing.T) {
	var rootFlag, subFlag string

	root := &Command{
		Use: "prog",
		Run: func(cmd *Command, args []string) {},
	}
	sub := &Command{
		Use: "sub",
		Run: func(cmd *Command, args []string) {},
	}
	root.AddCommand(sub)

	root.PersistentFlags().StringVar(&rootFlag, "flag", "defaultroot", "usage for root")
	sub.Flags().StringVar(&subFlag, "flag", "defaultsub", "usage for sub")

	buf := new(bytes.Buffer)
	root.SetOut(buf)
	root.SetErr(buf)
	root.SetArgs([]string{"help", "sub"})

	if err := root.Execute(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	output := buf.String()

	// The shadowing local flag's usage should appear in the help output.
	if !strings.Contains(output, "usage for sub") {
		t.Errorf("expected help output to show shadowing local flag (usage for sub), got:\n%s", output)
	}
}