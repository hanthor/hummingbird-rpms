package main

import (
	"crypto/fips140"
	"crypto/sha256"
	"fmt"
	"os"
)

func main() {
	if !fips140.Enabled() {
		os.Exit(1)
	}
	if got := fmt.Sprintf("%x", sha256.Sum256([]byte("rpmtest"))); len(got) != 64 {
		os.Exit(1)
	}
}
