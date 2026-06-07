# Fix file:// URL validation to require a valid path

The `isFileURL` validator was incorrectly accepting "file://" as valid even though it has no path component. This fix ensures that file URLs must have a non-empty path (other than just "/") to pass validation, making the behavior consistent with how "http://" already fails validation. The validator now checks that the parsed URL contains a meaningful path after successful parsing.
