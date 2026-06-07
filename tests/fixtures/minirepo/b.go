package minirepo

func B(c Config) string {
	if c.Verbose {
		return Hello("verbose-b")
	}
	return Hello("b")
}
