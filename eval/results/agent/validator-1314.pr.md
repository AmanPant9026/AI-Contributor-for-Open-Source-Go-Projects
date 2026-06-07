# Fix postcode_iso3166_alpha2_field validation by ensuring postcode regex initialization

The `postcode_iso3166_alpha2_field` validation was broken in v10.21.0 because the postcode regex patterns were not being initialized before use. This fix adds the missing `postcodeRegexInit.Do(initPostcodes)` call to ensure the regex patterns are loaded before attempting to validate postcodes against country-specific formats.
