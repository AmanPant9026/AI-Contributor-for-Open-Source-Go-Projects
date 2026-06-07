package minirepo

func Run() string {
	return A() + B(Config{Verbose: true})
}
