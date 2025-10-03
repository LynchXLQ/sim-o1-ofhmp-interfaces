#!/bin/bash

echo "Running network setup..."

# Function to clean interface names (remove @ifX suffix)
clean_iface_name() {
    echo "$1" | cut -d'@' -f1
}

# Check if CMD is empty and set a fallback
if [[ -z "$1" ]]; then
    echo "CMD is missing! Restoring default from the image."
    set -- /usr/bin/supervisord -c /etc/supervisor/conf.d/supervisord.conf
fi

# Identify interfaces based on their IP ranges
MACVLAN_IF=""
BRIDGE_IF=""

for RAW_IFACE in $(ip -o link show | awk -F': ' '!/lo/ {print $2}'); do
    IFACE=$(clean_iface_name "$RAW_IFACE")  # Remove @ifX suffix
    IP=$(ip -4 -o addr show dev "$IFACE" | awk '{print $4}' | cut -d'/' -f1)

    if [[ "$IP" == 172.99.* ]]; then
        MACVLAN_IF="$IFACE"
    else
        BRIDGE_IF="$IFACE"
    fi
done

# Ensure both interfaces were correctly identified
if [[ -z "$BRIDGE_IF" || -z "$MACVLAN_IF" ]]; then
    echo "Could not determine macvlan or bridge network interfaces. Exiting."
    exec "$@"
    exit 0
fi

echo "Macvlan interface: $MACVLAN_IF (IP range 172.99.x.x)"
echo "Bridge interface: $BRIDGE_IF (Internet access expected)"

# Rename interfaces properly (only if needed)
if [[ "$MACVLAN_IF" == "eth0" ]]; then
    echo "Renaming interfaces to ensure bridge is eth0..."
    
    # Bring interfaces down before renaming
    ip link set eth0 down
    ip link set eth1 down

    # Rename eth0 -> temp, eth1 -> eth0, temp -> eth1
    ip link set eth0 name tempeth
    ip link set eth1 name eth0
    ip link set tempeth name eth1

    # Bring interfaces back up
    ip link set eth0 up
    ip link set eth1 up

    # Update variable names (since we renamed them)
    TMP="$MACVLAN_IF"
    MACVLAN_IF="$BRIDGE_IF"
    BRIDGE_IF="$TMP"
fi

# Find the gateway of the bridge network
echo "Modifying default route to use $BRIDGE_IF for internet access..."
BRIDGE_GATEWAY=$(ip route | grep "default via" | grep "$BRIDGE_IF" | awk '{print $3}')

if [[ -z "$BRIDGE_GATEWAY" ]]; then
    # Fallback: Manually infer from subnet (assume .1 as gateway)
    BRIDGE_SUBNET=$(ip route | grep "dev $BRIDGE_IF" | awk '{print $1}')
    BRIDGE_GATEWAY=$(echo "$BRIDGE_SUBNET" | sed 's|0/.*|1|')
fi

# Ensure the gateway was found before modifying routes
if [[ -z "$BRIDGE_GATEWAY" ]]; then
    echo "Could not determine bridge network gateway. Exiting."
    exec "$@"
    exit 0
fi

echo "Bridge network gateway inferred as: $BRIDGE_GATEWAY"

# **Fix: Ensure default route exists before deleting**
if ip route | grep -q "default via"; then
    ip route del default
fi

# **Fix: Only add route if it does not already exist**
if ! ip route | grep -q "default via $BRIDGE_GATEWAY"; then
    ip route add default via "$BRIDGE_GATEWAY" dev "$BRIDGE_IF"
fi

echo "Network setup complete. Starting application..."

# Debug: Print CMD before executing
echo "Executing: $@"

# Execute CMD
exec "$@"
