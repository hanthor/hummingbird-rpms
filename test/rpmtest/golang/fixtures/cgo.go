package main

/*
int answer(void) { return 42; }
*/
import "C"
import "fmt"

func main() { fmt.Printf("rpmtest CGO works: %d\n", int(C.answer())) }
