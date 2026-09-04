#!/usr/bin/env sh
# Prints a greeting from each host.
set -eu

# runon exports these; they also let you run this script by hand.
echo "program : ${RUNON_PROGRAM:-hello-world}"
echo "host    : ${RUNON_HOST:-local}"

# Shared helpers live next door. Keep each one to a single job, and do not have
# them call each other — a function that calls a function is a call stack you
# will be debugging over ssh at some point.
. "${RUNON_FUNCTIONS:-$(cd "$(dirname "$0")/../../functions" && pwd)}/say.sh"

say "hello from $(hostname)"
