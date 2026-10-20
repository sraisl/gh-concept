package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHandlers(t *testing.T) {
	tests := []struct {
		path      string
		wantKey   string
		wantValue string
	}{
		{"/healthz", "status", "ok"},
		{"/hello", "message", "hello, world"},
		{"/hello?name=stage", "message", "hello, stage"},
		{"/hello?name=", "message", "hello, world"},
		{"/hello?name=Gr%C3%BC%C3%9Fe%20%22Docker%22", "message", "hello, Grüße \"Docker\""},
	}

	mux := newMux()
	for _, tt := range tests {
		t.Run(tt.path, func(t *testing.T) {
			rec := httptest.NewRecorder()
			mux.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, tt.path, nil))

			if rec.Code != http.StatusOK {
				t.Fatalf("status = %d, want %d", rec.Code, http.StatusOK)
			}
			if got := rec.Header().Get("Content-Type"); got != "application/json" {
				t.Errorf("Content-Type = %q, want application/json", got)
			}
			var body map[string]string
			if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
				t.Fatalf("decode body: %v", err)
			}
			if got := body[tt.wantKey]; got != tt.wantValue {
				t.Errorf("%s = %q, want %q", tt.wantKey, got, tt.wantValue)
			}
		})
	}
}
