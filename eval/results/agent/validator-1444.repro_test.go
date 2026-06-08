package validator

import (
	"testing"
)

func TestAgentRepro(t *testing.T) {
	validate := New()
	
	type TestStruct struct {
		URL string `validate:"url"`
	}
	
	// "file://" should fail validation because it has no path
	// This is similar to how "http://" already fails
	test := TestStruct{
		URL: "file://",
	}
	
	err := validate.Struct(test)
	if err == nil {
		t.Errorf("Expected validation error for 'file://' but got none")
	}
}