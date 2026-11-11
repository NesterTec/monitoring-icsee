#!/usr/bin/env bash
# Apply (or refresh) a site peer on the VPS WireGuard interface.
# Usage: apply-peer.sh <site_id> <tunnel_ip> <vpn_cidr> <peer_public_key>
# Invoked by the NVR when WG_APPLY_CMD points here (run on the host, not inside Docker).
set -euo pipefail

SITE_ID="${1:?site_id}"
TUNNEL_IP="${2:?tunnel_ip}"
VPN_CIDR="${3:?vpn_cidr}"
PUBKEY="${4:?public_key}"
IFACE="${WG_IFACE:-wg0}"
PEERS_DIR="${WG_PEERS_DIR:-/etc/wireguard/peers}"

mkdir -p "$PEERS_DIR"
SNIPPET="$PEERS_DIR/site-${SITE_ID}.conf"
cat >"$SNIPPET" <<EOF
# peer site id=${SITE_ID}
[Peer]
PublicKey = ${PUBKEY}
AllowedIPs = ${TUNNEL_IP}/32, ${VPN_CIDR}
PersistentKeepalive = 25
EOF

# Live update: remove old peer with same key if present, then add.
wg set "$IFACE" peer "$PUBKEY" allowed-ips "${TUNNEL_IP}/32,${VPN_CIDR}" persistent-keepalive 25
echo "Applied peer site-${SITE_ID} on ${IFACE}"
