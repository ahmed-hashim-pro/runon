#!/usr/bin/env sh
# Reports the fullest filesystem, and fails if it is above a threshold.
set -eu

THRESHOLD="${1:-90}"
. "${FLEETSH_FUNCTIONS:-../../functions}/say.sh"

say "checking disks (threshold ${THRESHOLD}%)"

# Find the capacity column by looking for the field that ends in "%", rather
# than assuming its position: df's column count differs between Linux and macOS,
# and a mount point containing a space shifts everything after it.
worst=$(df -P | awk '
    NR == 1 { next }
    # Pseudo-filesystems are permanently "full" and reporting them is noise:
    # devfs sits at 100% on every macOS machine that has ever booted.
    $1 ~ /^(devfs|tmpfs|map|overlay|udev)$/ { next }
    $NF ~ /^\/(dev|proc|sys|run)($|\/)/ { next }
    {
        for (i = 1; i <= NF; i++) {
            if ($i ~ /^[0-9]+%$/) {
                pct = $i; sub(/%/, "", pct)
                if (pct + 0 > max) { max = pct + 0; mount = $NF }
                break
            }
        }
    }
    END { printf "%d %s", max, mount }
')
used=${worst%% *}
mount=${worst#* }

say "highest usage: ${used}% on ${mount}"

if [ "${used}" -ge "${THRESHOLD}" ]; then
    say "OVER THRESHOLD"
    exit 1
fi

say "ok"
