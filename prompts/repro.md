You are fixing a bug in a Go library. Write ONE Go test that REPRODUCES the bug.

A correct reproduction test:
- FAILS when run against the current code (because the bug is present right now), and
- will PASS only once the bug is fixed.

So you must assert the behavior the user EXPECTS (what SHOULD happen) -- which is
currently broken. Do NOT assert the buggy behavior. If the report says a valid
input is wrongly rejected, your test must feed that valid input and assert it is
ACCEPTED (no error); that test fails today and passes after the fix.

Rules:
- Output ONLY a complete Go test file: no prose, no markdown fences.
- Use `package validator` unless the SOURCE shows otherwise.
- Name the test function exactly `TestAgentRepro`.
- Keep it small; standard library plus the package under test only.

BUG REPORT:
{problem_statement}

SOURCE:
{context}
{feedback}
