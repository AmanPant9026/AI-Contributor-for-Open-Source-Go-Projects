package validator

import (
	"context"
	"testing"
)

func TestAgentRepro(t *testing.T) {
	cmd := &Command{
		Use: "root",
		Run: func(cmd *Command, args []string) {},
	}

	type ctxKey string
	var key ctxKey = "k"
	ctx := context.WithValue(context.Background(), key, "value")
	cmd.SetContext(ctx)

	var gotCtx context.Context
	cmd.SetHelpFunc(func(c *Command, args []string) {
		gotCtx = c.Context()
	})

	if err := cmd.Help(); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	if gotCtx == nil {
		t.Fatalf("expected context to flow into help func, got nil")
	}
	if v := gotCtx.Value(key); v != "value" {
		t.Fatalf("expected context value %q, got %v", "value", v)
	}
}