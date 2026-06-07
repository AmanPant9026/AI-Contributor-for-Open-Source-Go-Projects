package validator

import (
	"strings"
	"testing"
)

func TestAgentRepro(t *testing.T) {
	validate := New()

	data := map[string]interface{}{"email": "emailaddress"}
	rules := map[string]interface{}{"email": "required,email"}
	errs := validate.ValidateMap(data, rules)

	if len(errs) == 0 {
		t.Fatal("expected validation error for invalid email")
	}

	emailErr, ok := errs["email"]
	if !ok {
		t.Fatal("expected error for 'email' field")
	}

	errStr := emailErr.(error).Error()
	
	// The bug is that the key is missing from the error message
	// After the fix, the error should contain "Key: 'email'"
	if !strings.Contains(errStr, "Key: 'email'") {
		t.Errorf("expected error to contain \"Key: 'email'\", got: %s", errStr)
	}
}