package gin

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestAgentRepro(t *testing.T) {
	SetMode(TestMode)

	router := New()
	router.Use(Recovery())
	router.GET("/", func(c *Context) {
		panic(http.ErrAbortHandler)
	})

	defer func() {
		r := recover()
		if r == nil {
			t.Fatal("expected http.ErrAbortHandler to be re-panicked, but recovery suppressed it")
		}
		if r != http.ErrAbortHandler {
			t.Fatalf("expected http.ErrAbortHandler, got %v", r)
		}
	}()

	w := httptest.NewRecorder()
	req := httptest.NewRequest(http.MethodGet, "/", nil)
	router.ServeHTTP(w, req)
}