# Shared by the RPM builder and CI artifact bootstrap. POSIX shell, download operations only.
rpmtest_download() (
    rpmtest_errors=$(mktemp)
    trap 'rm -f "${rpmtest_errors}"' EXIT
    rpmtest_attempt=1
    rpmtest_delay=2
    rpmtest_transient='curl: \((5|6|7|18|28|35|52|55|56|92)\)|Curl error \((5|6|7|18|28|35|52|55|56|92)\)|could not resolve|couldn.t resolve|no such host|temporary failure in name resolution|connection (refused|reset|timed out|closed)|i/o timeout|context deadline exceeded|TLS handshake timeout|(unexpected |: )EOF|network is unreachable|(HTTP(/[0-9.]+)?[[:space:]]+|returned error:[[:space:]]+|status([[:space:]]+code)?[:=]?[[:space:]]+)(408|429|5[0-9]{2})([^0-9]|$)'
    rpmtest_permanent='unauthorized|authentication required|permission denied|forbidden|manifest unknown|name unknown|no match for argument|unable to find a match|nothing provides|conflicting requests|certificate verify failed|SSL peer certificate|status([[:space:]]+code)?[:=]?[[:space:]]+(401|403|404)([^0-9]|$)'
    while :; do
        if "$@" 2>"${rpmtest_errors}"; then
            cat "${rpmtest_errors}" >&2
            exit 0
        else
            rpmtest_status=$?
        fi
        cat "${rpmtest_errors}" >&2
        if [ "${rpmtest_attempt}" -ge 4 ] || [ "${rpmtest_status}" -ge 128 ] ||
            grep -Eiq "${rpmtest_permanent}" "${rpmtest_errors}"; then
            exit "${rpmtest_status}"
        fi
        if [ "${rpmtest_status}" -ne 124 ] &&
            ! grep -Eiq "${rpmtest_transient}" "${rpmtest_errors}"; then
            exit "${rpmtest_status}"
        fi
        echo "Download attempt ${rpmtest_attempt}/4 failed; retrying in ${rpmtest_delay}s." >&2
        sleep "${rpmtest_delay}"
        rpmtest_attempt=$((rpmtest_attempt + 1))
        rpmtest_delay=$((rpmtest_delay * 2))
    done
)
