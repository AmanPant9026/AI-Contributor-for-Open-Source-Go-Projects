package minirepo

import "fmt"

// Hello is referenced by a.go, b.go and main.go (the central symbol).
func Hello(name string) string {
	return fmt.Sprintf("hello %s", name)
}

// Config is referenced by b.go.
type Config struct {
	Verbose bool
}
