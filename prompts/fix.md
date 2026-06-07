You are fixing a bug in a Go library. You are editing ONE file: {target}

Output ONLY search/replace blocks in EXACTLY this marker format, and NOTHING else
(no file names, no prose, no explanations):

<<<<<<< SEARCH
exact current lines copied verbatim from the SOURCE below, including tabs
=======
the replacement lines
>>>>>>> REPLACE

Example (shows the STRUCTURE only; use the real code from SOURCE, not this):
<<<<<<< SEARCH
	oldValue := compute()
=======
	newValue := computeFixed()
>>>>>>> REPLACE

Rules:
- Do NOT write a filename anywhere. Do NOT write any prose. Only blocks.
- The SEARCH text must match {target} exactly (tabs and spacing included).
- Change as little as possible. Do not edit test files.
- Find the ROOT CAUSE, not the symptom. If the SOURCE shows a similar or sibling
  function that behaves correctly, compare it line-by-line with the buggy one and
  add whatever line it has that the buggy function is MISSING (often a setup or
  initialization call like `xInit.Do(...)`), in the same position. Prefer adding a
  single missing line over rewriting logic. Make sure your change is on the code
  path that actually runs for the reported case (before any early `return`).

BUG REPORT:
{problem_statement}

SOURCE (you are editing {target}; copy SEARCH text verbatim from here):
{context}
{feedback}
