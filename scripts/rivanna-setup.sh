#!/bin/bash

set -Eeuo pipefail

PROJECT="variant-list-optimizer"
VERSION="2026-03-16-1"

PROJECT_ROOT="/project/bii_nssac/people/pb5gj/shared"
PROJECT_DIR="$PROJECT_ROOT/$PROJECT/$VERSION"

BUILD_ROOT="/scratch/$USER/build/$PROJECT/$VERSION"
BUILD_DIR="$BUILD_ROOT/build/Release"

run_build() {
    rm -rf "$BUILD_DIR"
    rm -f compile_commands.json

    conan install cpp --build=missing --output-folder="$BUILD_ROOT"

    cmake -S cpp -B "$BUILD_DIR" \
        -DCMAKE_CXX_FLAGS="-g3 -Wall -Wextra" \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_EXPORT_COMPILE_COMMANDS=ON \
        -DCMAKE_TOOLCHAIN_FILE="generators/conan_toolchain.cmake"

    ln -s "$BUILD_DIR/compile_commands.json"

    cmake --build "$BUILD_DIR" --parallel
}

run_install() {
    mkdir -p "$PROJECT_DIR/bin"
    chmod 755 "$PROJECT_DIR/bin"

    cp "$BUILD_DIR/compute-optimal-tlist"  "$PROJECT_DIR/bin"
}

show_help() {
    echo "Usage: $0 (help | command)"
}

if [[ "$1" == "help" || "$1" == "-h" || "$1" == "--help" ]]; then
    show_help
elif [[ $(type -t "run_${1}") == function ]]; then
    fn="run_${1}"
    shift
    $fn "$@"
else
    echo "Unknown command: $1"
fi
