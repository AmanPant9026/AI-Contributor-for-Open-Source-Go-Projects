package validator

import (
	"bytes"
	"strings"
	"testing"
)

func TestAgentRepro(t *testing.T) {
	rootCmd := &Command{
		Use: "root",
		Run: func(cmd *Command, args []string) {},
	}
	rootCmd.PersistentFlags().String("flag", "", "required persistent")
	if err := rootCmd.MarkPersistentFlagRequired("flag"); err != nil {
		t.Fatalf("unexpected error marking flag required: %v", err)
	}

	buf := new(bytes.Buffer)
	rootCmd.SetOut(buf)
	rootCmd.SetErr(buf)
	rootCmd.SetArgs([]string{"__complete", ""})

	if _, err := rootCmd.ExecuteC(); err != nil {
		t.Fatalf("__complete should not fail due to required persistent flag, got error: %v", err)
	}

	out := buf.String()
	if strings.Contains(out, `required flag(s) "flag" not set`) {
		t.Fatalf("__complete wrongly reported required flag error: %q", out)
	}
}