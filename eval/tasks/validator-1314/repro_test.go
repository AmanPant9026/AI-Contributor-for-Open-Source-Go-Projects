package validator

import "testing"

// Reproduction for go-playground/validator issue #1314:
// postcode_iso3166_alpha2_field validation was broken in v10.21.0 because the
// postcode regexes were never lazily initialised. A valid US postcode should
// pass; before the fix (PR #1359) it fails.
func TestIssue1314PostcodeIso3166Alpha2Field(t *testing.T) {
	type Example struct {
		PostCode    string `validate:"required,postcode_iso3166_alpha2_field=CountryCode"`
		CountryCode string `validate:"required,iso3166_1_alpha2"`
	}

	validate := New(WithRequiredStructEnabled())

	ex := Example{CountryCode: "US", PostCode: "12345"}
	if err := validate.Struct(ex); err != nil {
		t.Fatalf("expected valid US postcode to pass, got: %v", err)
	}
}
