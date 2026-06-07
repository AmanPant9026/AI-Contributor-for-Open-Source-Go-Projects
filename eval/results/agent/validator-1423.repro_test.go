package validator

import (
	"testing"
)

func TestAgentRepro(t *testing.T) {
	validate := New()

	type testStruct struct {
		privateField string `validate:"required"`
	}

	s := testStruct{
		privateField: "value",
	}

	err := validate.Struct(s)
	if err != nil {
		t.Errorf("Expected no error when validating struct with private field, but got: %v", err)
	}
}