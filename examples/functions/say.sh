#!/usr/bin/env sh
# Prints a message with a consistent prefix. One job, no nesting.
say() {
    printf '[%s] %s\n' "${RUNON_HOST:-local}" "$1"
}
