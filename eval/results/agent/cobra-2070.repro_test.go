package validator

import (
	"bytes"
	"testing"
)

func TestAgentRepro(t *testing.T) {
	// When used as a plugin, a subcommand's ExecuteC may be called directly.
	// The expected behavior is that errors from the subcommand are routed to
	// the subcommand's own output writer, not the root's.
	root := &Command{
		Use: "root",
		Run: func(cmd *Command, args []string) {},
	}

	subErrBuf := new(bytes.Buffer)
	sub := &Command{
		Use:  "sub",
		Args: ExactArgs(1),
		Run:  func(cmd *Command, args []string) {},
	}
	sub.SetErr(subErrBuf)

	root.AddCommand(sub)

	rootErrBuf := new(bytes.Buffer)
	root.SetErr(rootErrBuf)

	// Invoke with no args to trigger the ExactArgs(1) validation error.
	root.SetArgs([]string{"sub"})

	_, err := root.ExecuteC()
	if err == nil {
		t.Fatal("expected an error from sub command args validation")
	}

	// The error should be written to the sub command's error writer,
	// since the error originated from the sub command. Currently the
	// root's error writer is used instead (the bug).
	if subErrBuf.Len() == 0 {
		t.Fatalf("expected error to be written to sub command's error writer, but got nothing; root buffer got: %q", rootErrBuf.String())
	}
}