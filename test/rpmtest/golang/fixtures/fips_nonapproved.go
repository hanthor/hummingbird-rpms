package main

import "crypto/md5"

func main() {
	_ = md5.Sum([]byte("rpmtest"))
}
