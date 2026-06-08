# Fix file:// URL validation to require a path component

The `file://` URL was incorrectly passing validation despite having no path component, similar to how `http://` correctly fails validation. The fix adds a check to ensure that parsed file URLs have a non-empty path, making `file://` fail validation while valid file URLs like `file:///path/to/file` continue to pass.
