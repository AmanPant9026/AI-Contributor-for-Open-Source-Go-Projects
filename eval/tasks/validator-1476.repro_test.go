package validator

import "testing"

// Reproduction for validator PR #1476: the e164 regex accepted numbers whose
// country code starts with 0 (e.g. "+0123456789"). E.164 country codes never
// start with 0, so these must be rejected. Before the fix they were accepted;
// the PR's own TestE164 does not cover this case, so we target it directly.
func TestPR1476E164RejectsLeadingZero(t *testing.T) {
	validate := New()

	if err := validate.Var("+0123456789", "e164"); err == nil {
		t.Fatalf("expected +0123456789 to be rejected by e164, but it was accepted")
	}
	// a clearly valid E.164 number must still pass
	if err := validate.Var("+12025550123", "e164"); err != nil {
		t.Fatalf("expected +12025550123 to be valid e164, got: %v", err)
	}
}
