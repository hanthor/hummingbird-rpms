package calc

import "testing"

func TestAdd(t *testing.T) {
	if got := Add(19, 23); got != 42 {
		t.Fatalf("Add(19, 23) = %d, want 42", got)
	}
}
