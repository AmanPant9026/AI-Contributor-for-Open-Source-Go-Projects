package validator

import (
	"testing"
)

func TestAgentRepro(t *testing.T) {
	type Example struct {
		PostCode    string `validate:"required,postcode_iso3166_alpha2_field=CountryCode"`
		CountryCode string `validate:"required,iso3166_1_alpha2"`
	}

	validate := New(WithRequiredStructEnabled())
	ex := Example{CountryCode: "US", PostCode: "12345"}
	err := validate.Struct(ex)

	if err != nil {
		t.Errorf("Expected validation to pass for valid US postcode '12345', but got error: %v", err)
	}
}