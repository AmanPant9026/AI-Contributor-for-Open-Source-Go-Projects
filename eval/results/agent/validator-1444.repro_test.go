package validator

import "testing"

func TestAgentRepro(t *testing.T) {
	validate := New()
	
	type TestStruct struct {
		URL string `validate:"url"`
	}
	
	test := TestStruct{
		URL: "file://",
	}
	
	err := validate.Struct(test)
	if err == nil {
		t.Error("Expected validation error for 'file://' but got none")
	}
}