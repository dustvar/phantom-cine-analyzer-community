"""Add saved macOS network services to a dedicated Phantom Ethernet adapter."""
from ipaddress import IPv4Address, IPv4Interface, IPv4Network
import re
from shlex import join


FACTORY_NETWORKS = (IPv4Network('100.100.0.0/16'), IPv4Network('172.16.0.0/16'))


def _interface(value):
    if isinstance(value, IPv4Interface):
        return value
    return IPv4Interface(str(value))


# networksetup accepts a BSD device name when creating a service. Keeping a
# separate service per subnet preserves the adapter's original IP and gateway.
# Only remove services created by this invocation if configuration fails.
_SETUP = r"""
set -eu
export LC_ALL=C
ns=/usr/sbin/networksetup
device=$1
shift
created=''
cleanup() {
    result=$?
    trap - EXIT
    if [ "$result" != 0 ]; then
        for prefix in $created; do
            "$ns" -removenetworkservice "PCA Camera $device ($prefix)" >/dev/null 2>&1 || :
        done
    fi
    exit "$result"
}
trap cleanup EXIT
fail() { echo "$1" >&2; exit 1; }
/sbin/ifconfig "$device" | /usr/bin/grep -q 'status: active' || fail 'Reconnect the camera cable and try again.'
wifi=$("$ns" -listallhardwareports | /usr/bin/awk '/Hardware Port: (Wi-Fi|AirPort)$/ {getline; print $2}')
[ "$wifi" != "$device" ] || fail 'Choose a wired Ethernet adapter.'
for address do
    prefix=${address%.*.*}
    service="PCA Camera $device ($prefix)"
    if "$ns" -listallnetworkservices | /usr/bin/grep -Fxq -e "$service" -e "*$service"; then
        "$ns" -listnetworkserviceorder | /usr/bin/awk -v name="$service" -v dev="$device" '
            {line=$0; sub(/^\([0-9]+\) /, "", line)}
            line==name {getline; if (index($0, ", Device: " dev ")")) ok=1}
            END {exit !ok}' || fail 'A network service with this name belongs to another adapter. Review Network settings.'
        "$ns" -getnetworkserviceenabled "$service" | /usr/bin/grep -Fxq Enabled || fail 'The saved camera service is disabled in Network settings.'
    else
        result=$("$ns" -createnetworkservice "$service" "$device") || fail "$result"
        case "$result" in *'** Error:'*) fail "$result";; esac
        created="$created $prefix"
        "$ns" -setmanual "$service" "$address" 255.255.0.0 "" || fail 'macOS could not set the camera-network address.'
        "$ns" -setv6off "$service" || fail 'macOS could not finish the camera-network service.'
    fi
    info=$("$ns" -getinfo "$service")
    echo "$info" | /usr/bin/grep -Fqx "IP address: $address" || fail 'A saved service uses different settings. Review Network settings.'
    echo "$info" | /usr/bin/grep -Fqx 'Subnet mask: 255.255.0.0' || fail 'macOS did not save the camera network mask.'
done
"""


def setup_arguments(device, addresses, local_addresses, *, camera_host=''):
    """Return osascript arguments. System authentication happens only when run."""
    if not re.fullmatch(r'en[0-9]{1,4}', device):
        raise ValueError('Choose a connected Ethernet adapter.')
    selected = tuple(_interface(address) for address in addresses)
    peer = IPv4Address(camera_host) if camera_host else None
    selected_ips = {address.ip for address in selected}
    others = [_interface(address) for address in local_addresses]
    missing = [net for net in FACTORY_NETWORKS
               if not any(address.network == net for address in selected)]
    if not missing:
        raise ValueError('This adapter already covers both Phantom camera networks.')
    host = next((int(address.ip) & 0xffff for address in selected
                 if any(address.ip in net for net in FACTORY_NETWORKS)), 0x0607)
    if host in (0, 65535):
        host = 0x0607
    requested = []
    for net in missing:
        if any(address.ip not in selected_ips and address.network.overlaps(net) for address in others):
            raise ValueError(f'Another network interface already uses part of {net}. Review Network settings before adding it here.')
        proposed = net.network_address + host
        if proposed == peer:
            proposed = net.network_address + (host + 1 if host < 65534 else host - 1)
        if any(address.ip in net for address in selected):
            raise ValueError(f'This adapter already has an address in {net} with a different subnet mask. Review its Network settings.')
        requested.append(str(proposed))
    # All dynamic shell arguments are validated device names or generated IPv4s.
    command = join(('/bin/sh', '-c', _SETUP, 'pca-network', device, *requested))
    return ['-e', 'on run argv', '-e',
            'do shell script (item 1 of argv) with administrator privileges with prompt '
            '"Save the Phantom camera networks on the selected Ethernet adapter."',
            '-e', 'end run', command]
