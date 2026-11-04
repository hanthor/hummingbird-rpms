package main

import (
	"crypto/sha256"
	"fmt"
)

// Import only a crypto entry point so the startup check must run without
// an application importing crypto/fips140 itself.
func main() {
	fmt.Printf("%x\n", sha256.Sum256([]byte("startup guard")))
}
