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
			phoneNumber: "+12345678901",
			shouldFail:  false,
		},
		{
			name:        "invalid E.164 phone number starting with +0",
			phoneNumber: "+01234567890",
			shouldFail:  true,
		},
		{
			name:        "invalid E.164 phone number starting with +00",
			phoneNumber: "+00123456789",
			shouldFail:  true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := validate.Var(tt.phoneNumber, "e164")
			
			if tt.shouldFail {
				if err == nil {
					t.Errorf("Expected validation to fail for %s, but it passed", tt.phoneNumber)
				}
			} else {
				if err != nil {
					t.Errorf("Expected validation to pass for %s, but it failed: %v", tt.phoneNumber, err)
				}
			}
		})
	}
}