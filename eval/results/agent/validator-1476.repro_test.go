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
			name:        "valid phone number with +1",
			phoneNumber: "+12345678901",
			shouldFail:  false,
		},
		{
			name:        "valid phone number with +44",
			phoneNumber: "+442071838750",
			shouldFail:  false,
		},
		{
			name:        "invalid phone number starting with +0",
			phoneNumber: "+01234567890",
			shouldFail:  true,
		},
		{
			name:        "invalid phone number starting with +00",
			phoneNumber: "+001234567890",
			shouldFail:  true,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := validate.Var(tt.phoneNumber, "e164")
			
			if tt.shouldFail {
				if err == nil {
					t.Errorf("expected validation to fail for %s, but it passed", tt.phoneNumber)
				}
			} else {
				if err != nil {
					t.Errorf("expected validation to pass for %s, but got error: %v", tt.phoneNumber, err)
				}
			}
		})
	}
}