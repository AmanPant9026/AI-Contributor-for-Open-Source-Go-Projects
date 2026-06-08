package validator

import (
	"testing"
)

type testStructWithPrivateField struct {
	publicField  string `validate:"required"`
	privateField string `validate:"required"`
}

func TestAgentRepro(t *testing.T) {
	validate := New()

	s := testStructWithPrivateField{
		publicField:  "public",
		privateField: "private",
	}

	err := validate.Struct(s)
	if err != nil {
		t.Errorf("Expected no error when validating struct with private fields, got: %v", err)
	}
}