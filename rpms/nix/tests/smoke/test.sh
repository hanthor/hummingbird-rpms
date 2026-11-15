#!/bin/bash

set -x -e

nix --version

nix-shell -p hello --command hello

( cd examples/default
  nix-build
)

( cd examples/shell
  nix-shell --command "bash --version"
)

nix run nixpkgs#hello
