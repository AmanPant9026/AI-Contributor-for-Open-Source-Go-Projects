package binding

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"testing"
)

type EmptyValueStruct struct {
	Name  string `json:"name" binding:"required"`
	Value string `json:"value"`
}

func TestAgentRepro(t *testing.T) {
	// Test that empty string value (not missing field) should be accepted
	// when the field is not marked as required
	jsonData := []byte(`{"name":"test","value":""}`)
	
	req := httptest.NewRequest(http.MethodPost, "/", bytes.NewBuffer(jsonData))
	req.Header.Set("Content-Type", "application/json")
	
	var obj EmptyValueStruct
	err := JSON.Bind(req, &obj)
	
	// Empty value should be accepted (no error expected)
	if err != nil {
		t.Errorf("Expected no error for empty value field, got: %v", err)
	}
	
	if obj.Name != "test" {
		t.Errorf("Expected name to be 'test', got: %s", obj.Name)
	}
	
	if obj.Value != "" {
		t.Errorf("Expected value to be empty string, got: %s", obj.Value)
	}
}