package main

import (
	"crypto/mldsa"
	"fmt"
	"os"
)

func main() {
	for _, params := range []mldsa.Parameters{mldsa.MLDSA44(), mldsa.MLDSA65(), mldsa.MLDSA87()} {
		_, generateErr := mldsa.GenerateKey(params)
		_, privateErr := mldsa.NewPrivateKey(params, make([]byte, mldsa.PrivateKeySize))
		_, publicErr := mldsa.NewPublicKey(params, make([]byte, params.PublicKeySize()))
		verifyErr := mldsa.Verify(&mldsa.PublicKey{}, nil, nil, nil)
		if generateErr == nil || privateErr == nil || publicErr == nil || verifyErr == nil {
			fmt.Fprintf(os.Stderr, "%s unexpectedly available with the certified module\n", params)
			os.Exit(1)
		}
		fmt.Printf("PASS: %s unavailable with the certified module\n", params)
	}
}
