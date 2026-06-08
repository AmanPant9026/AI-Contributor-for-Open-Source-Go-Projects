You are localizing a bug in a Go library. A reproduction test that triggers the bug
was run under coverage, and it executed the files listed below. The code that must
change to fix the bug is in one (or a few) of them.

BUG REPORT:
{problem_statement}

WHAT HAS ALREADY BEEN READ FROM THE CODE (snippets, with their file paths):
{context}

CANDIDATE FILES (the failing test executed these -- the bug is among them):
{candidates}

Which files most likely contain the code that must change? Reply with filenames ONLY,
the single most likely first, one per line, no numbering and no explanation. Use only
filenames from the candidate list above. List at most four.
