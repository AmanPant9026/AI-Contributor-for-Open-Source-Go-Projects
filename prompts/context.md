You are localizing a bug in a Go library before fixing it. You may ask to read
specific code. Respond with ONE action per turn, nothing else:

  READ <relative/path.go> <start_line> <end_line>     to read lines of a file
  SEARCH <regex>                                       to search the repo
  DONE                                                 when you have enough context

BUG REPORT:
{problem_statement}

REPOSITORY MAP (most important files first):
{repo_map}

CANDIDATE LOCATIONS:
{candidates}

SO FAR YOU HAVE SEEN:
{seen}
