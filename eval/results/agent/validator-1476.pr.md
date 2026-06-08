# Fix E.164 phone validation to reject country codes starting with +0

The E.164 phone number validation regex incorrectly allowed country codes starting with +0 (e.g., +012345678), which violates the E.164 standard where country codes must start with digits 1-9. The fix removes the optional quantifier `?` after `[1-9]` in the regex pattern, ensuring at least one non-zero digit is required after the `+` sign. A unit test has been added to verify the corrected validation behavior.
