package validator

import (
	"testing"
)

func TestAgentRepro(t *testing.T) {
	validate := New()

	tests := []struct {
		name        string
		phoneNumber string
		shouldFail  bool
	}{
		{
			name:        "valid E.164 phone number",
			phoneNumber: "+14155552671",
			shouldFail:  false,
		},
		{
			name:        "invalid E.164 phone number starting with +0",
			phoneNumber: "+01234567890",
			shouldFail:  true,
		},
		{
			name:        "invalid E.164 phone number starting with +00",
			phoneNumber: "+001234567890",
			shouldFail:  true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			type TestStruct struct {
				Phone string `validate:"e164"`
			}

			testData := TestStruct{
				Phone: tt.phoneNumber,
			}

			err := validate.Struct(testData)

			if tt.shouldFail {
				if err == nil {
					t.Errorf("Expected validation to fail for phone number %s, but it passed", tt.phoneNumber)
				}
			} else {
				if err != nil {
					t.Errorf("Expected validation to pass for phone number %s, but it failed: %v", tt.phoneNumber, err)
				}
			}
		})
	}
}