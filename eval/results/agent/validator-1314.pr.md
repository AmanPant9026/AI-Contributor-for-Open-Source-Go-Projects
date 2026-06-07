# Fix postcode_iso3166_alpha2_field validation by initializing postcode regex map

The `isPostcodeByIso3166Alpha2Field` function was failing because the postcode regex map was not initialized before use. PR #1270 introduced a lazy initialization pattern but failed to call the initialization in this validation function. This fix adds the missing `postcodeRegexInit.Do(initPostcodes)` call to ensure the postcode patterns are loaded before validation occurs.
