# Fix missing keys in map validation errors by adding VarWithKeyCtx method

The `ValidateMapCtx` function was calling `VarCtx` which returned validation errors without field keys, making error messages unhelpful (e.g., "Key: '' Error:Field validation for '' failed"). This fix introduces a new `VarWithKeyCtx` method that accepts a key parameter and includes it in the validation error namespace. The existing `VarCtx` method now delegates to `VarWithKeyCtx` with an empty key to maintain backward compatibility, while `ValidateMapCtx` uses the new method to provide properly keyed error messages.
