"""
O-RAN User Plane Configuration Feature Implementation.

Implements carrier management functionality from o-ran-uplane-conf YANG module:
- Carrier activation/deactivation (tx-array-carriers, rx-array-carriers)
- Carrier state notifications
- Active state management (ACTIVE, INACTIVE, SLEEP)
- Antenna mask control (tx-array-antenna-mask-config, rx-array-antenna-mask-config)
- Energy sharing groups (energy-sharing-groups-disabled)

Reference: O-RAN.WG4.MP.0-v13.00 and O-RAN.WG4.TS.CONF.0-R004-v13.00 Section 3.1.14.1/3.1.14.2/3.1.14.4/3.1.14.6
"""

from util.logging import get_pynts_logger
import threading
import re
from datetime import datetime, timezone
from core.netconf import Netconf, Datastore
from util.threading import stop_event

logger = get_pynts_logger("feature-o-ran-uplane-conf")


class ORanUplaneConfFeature:
    """
    O-RAN User Plane Configuration feature implementation.

    Handles carrier activation/deactivation, antenna mask control,
    energy sharing groups, and generates appropriate notifications.
    """

    def __init__(self) -> None:
        self.netconf = Netconf()
        self.carriers = {}  # carrier_name -> {tx_state, rx_state}
        self.antenna_masks = {}  # array_name -> {antenna_bitmask, antenna_bitmask_index}
        self.energy_sharing_groups_disabled = []  # List of disabled group IDs
        self.lock = threading.Lock()

    def start(self) -> None:
        """Start the uplane-conf feature by subscribing to config changes."""
        logger.info("Starting O-RAN user plane configuration feature")
        try:
            # Initialize carrier states
            self._initialize_carrier_states()

            # Subscribe to uplane-conf configuration changes
            self.netconf.running.subscribe_module_change(
                "o-ran-uplane-conf",
                None,  # xpath filter (None = all changes)
                self._handle_config_change
            )
            logger.info("Successfully subscribed to o-ran-uplane-conf module changes")

        except Exception as e:
            logger.error(f"Failed to start uplane-conf feature: {e}")

    def _initialize_carrier_states(self):
        """Initialize carrier states from running configuration."""
        try:
            # Default carrier state
            default_carrier = {
                "tx_active": "INACTIVE",
                "rx_active": "INACTIVE",
                "tx_state": "DISABLED",
                "rx_state": "DISABLED"
            }

            # Initialize carrier-1 as default
            with self.lock:
                self.carriers["carrier-1"] = default_carrier.copy()

            # Update operational state
            self._update_carrier_operational_state("carrier-1")
            logger.info("Initialized carrier states")

        except Exception as e:
            logger.error(f"Failed to initialize carrier states: {e}")

    def _handle_config_change(self, event, request_id, changes, private_data):
        """
        Handle configuration changes for o-ran-uplane-conf module.

        Watches for changes to:
        - carrier active state
        - antenna mask configuration
        - energy sharing groups disabled
        """
        logger.info(f"Uplane-conf config change event: {event}")

        # Only validate eAxC uniqueness during CHANGE event, skip all other processing
        event_str = str(event)
        if "CHANGE" in event_str and "DONE" not in event_str:
            try:
                has_eaxc = False
                for change in changes:
                    change_str = str(change)
                    if "eaxc-id" in change_str or "e-axcid" in change_str:
                        has_eaxc = True
                        break
                if has_eaxc:
                    self._validate_eaxc_uniqueness(changes)
            except ValueError:
                raise  # Re-raise to reject duplicate eAxC IDs
            except Exception:
                pass  # Don't block edit-config for non-validation errors
            return  # Always return cleanly for CHANGE events

        try:
            for change in changes:
                logger.debug(f"Change: {change}")

                change_str = str(change)

                # Check for tx-array-carrier active state change
                if "tx-array-carriers" in change_str and "active" in change_str:
                    carrier_name, new_state = self._extract_carrier_change(change, "tx")
                    if carrier_name:
                        self._update_carrier_state(carrier_name, "tx", new_state)

                # Check for rx-array-carrier active state change
                if "rx-array-carriers" in change_str and "active" in change_str:
                    carrier_name, new_state = self._extract_carrier_change(change, "rx")
                    if carrier_name:
                        self._update_carrier_state(carrier_name, "rx", new_state)

                # Check for antenna mask configuration changes
                if "antenna-mask-config" in change_str or "antenna-bitmask" in change_str:
                    self._handle_antenna_mask_change(change, change_str)

                # Check for energy sharing groups disabled changes
                if "energy-sharing-groups-disabled" in change_str:
                    self._handle_energy_sharing_group_change(change, change_str)

            # After processing changes, check for eAxC uniqueness
            for change in changes:
                change_str = str(change)
                if "eaxc-id" in change_str or "e-axcid" in change_str:
                    self._validate_eaxc_uniqueness(changes)
                    break

            # Send config-change notification after validation passes
            self._send_config_change_notification()

        except ValueError:
            # eAxC uniqueness violation — reject the edit-config
            raise
        except Exception as e:
            # Other errors should not block the edit-config
            logger.error(f"Error in config change handler (non-blocking): {e}")

    def _send_config_change_notification(self):
        """Send ietf-netconf-notifications:netconf-config-change notification in background."""
        import time as _time

        def _send():
            _time.sleep(1)  # Delay to let sysrepo commit finish
            try:
                notification_data = {
                    "changed-by": {"server": None},
                    "datastore": "running"
                }
                self.netconf.running.notification_send(
                    "/ietf-netconf-notifications:netconf-config-change",
                    notification_data
                )
                logger.info("Sent netconf-config-change notification")
            except Exception as e:
                logger.debug(f"Could not send config-change notification: {e}")

        t = threading.Thread(target=_send, daemon=True)
        t.start()

    def _validate_eaxc_uniqueness(self, changes):
        """Validate eAxC ID uniqueness per endpoint type per O-RAN spec.

        Per spec 3.1.10.1: TX and RX endpoints may share the same eAxC IDs.
        Uniqueness is only required WITHIN each endpoint type (all TX unique,
        all RX unique). Duplicates within the same type are rejected.
        """
        try:
            tx_ids = []
            rx_ids = []
            current_type = None

            for change in changes:
                change_str = str(change)
                # Track which endpoint type we're in
                if "low-level-tx" in change_str:
                    current_type = "tx"
                elif "low-level-rx" in change_str:
                    current_type = "rx"

                # Extract eAxC ID
                eid_match = re.search(r'eaxc-id["\'\s:=]+(\d+)', change_str)
                if eid_match and current_type:
                    eid = int(eid_match.group(1))
                    if current_type == "tx":
                        tx_ids.append(eid)
                    else:
                        rx_ids.append(eid)

            # Check duplicates within TX
            if len(tx_ids) != len(set(tx_ids)):
                dupes = [x for x in tx_ids if tx_ids.count(x) > 1]
                raise ValueError(f"Duplicate eAxC ID {dupes[0]} in TX endpoints")

            # Check duplicates within RX
            if len(rx_ids) != len(set(rx_ids)):
                dupes = [x for x in rx_ids if rx_ids.count(x) > 1]
                raise ValueError(f"Duplicate eAxC ID {dupes[0]} in RX endpoints")

            logger.debug(f"eAxC uniqueness validated: TX={len(tx_ids)}, RX={len(rx_ids)}")

        except ValueError:
            raise
        except Exception as e:
            logger.debug(f"Could not validate eAxC uniqueness: {e}")

    def _extract_carrier_change(self, change, carrier_type: str):
        """
        Extract carrier name and new state from change event.

        Args:
            change: Configuration change event
            carrier_type: "tx" or "rx"

        Returns:
            Tuple of (carrier_name, new_state) or (None, None) if not applicable
        """
        try:
            # Parse the change to extract carrier name and state
            change_str = str(change)

            # Extract carrier name (typically carrier-1, carrier-2, etc.)
            carrier_name = None
            if "name" in change_str:
                # Try to find the carrier name
                import re
                match = re.search(r"name['\"]?\s*[:=]\s*['\"]?([^'\">\s]+)", change_str)
                if match:
                    carrier_name = match.group(1)

            if not carrier_name:
                carrier_name = "carrier-1"  # Default

            # Extract active state
            new_state = "INACTIVE"  # Default
            if "ACTIVE" in change_str.upper():
                if "INACTIVE" not in change_str.upper():
                    new_state = "ACTIVE"
                else:
                    new_state = "INACTIVE"
            elif "SLEEP" in change_str.upper():
                new_state = "SLEEP"

            return carrier_name, new_state

        except Exception as e:
            logger.error(f"Error extracting carrier change: {e}")
            return None, None

    def _update_carrier_state(self, carrier_name: str, carrier_type: str, new_state: str):
        """
        Update carrier state and send notification.

        Args:
            carrier_name: Name of the carrier
            carrier_type: "tx" or "rx"
            new_state: New active state (ACTIVE, INACTIVE, SLEEP)
        """
        try:
            with self.lock:
                if carrier_name not in self.carriers:
                    self.carriers[carrier_name] = {
                        "tx_active": "INACTIVE",
                        "rx_active": "INACTIVE",
                        "tx_state": "DISABLED",
                        "rx_state": "DISABLED"
                    }

                old_state = self.carriers[carrier_name][f"{carrier_type}_active"]
                self.carriers[carrier_name][f"{carrier_type}_active"] = new_state

                # Update derived state
                if new_state == "ACTIVE":
                    self.carriers[carrier_name][f"{carrier_type}_state"] = "READY"
                elif new_state == "INACTIVE":
                    self.carriers[carrier_name][f"{carrier_type}_state"] = "DISABLED"
                elif new_state == "SLEEP":
                    self.carriers[carrier_name][f"{carrier_type}_state"] = "READY"

            if old_state != new_state:
                logger.info(f"Carrier {carrier_name} {carrier_type} state: {old_state} -> {new_state}")

                # Update operational state
                self._update_carrier_operational_state(carrier_name)

                # Send state change notification
                self._send_carrier_state_notification(carrier_name, carrier_type, new_state)

        except Exception as e:
            logger.error(f"Error updating carrier state: {e}")

    def _update_carrier_operational_state(self, carrier_name: str):
        """Update carrier operational state in datastore."""
        try:
            with self.lock:
                carrier_data = self.carriers.get(carrier_name, {})
                tx_active = carrier_data.get("tx_active", "INACTIVE")
                rx_active = carrier_data.get("rx_active", "INACTIVE")
                tx_state = carrier_data.get("tx_state", "DISABLED")
                rx_state = carrier_data.get("rx_state", "DISABLED")

            operational_data = {
                "user-plane-configuration": {
                    "tx-array-carriers": [
                        {
                            "name": carrier_name,
                            "active": tx_active,
                            "state": tx_state
                        }
                    ],
                    "rx-array-carriers": [
                        {
                            "name": carrier_name,
                            "active": rx_active,
                            "state": rx_state
                        }
                    ]
                }
            }

            self.netconf.set_data(Datastore.OPERATIONAL, "o-ran-uplane-conf", operational_data)
            logger.debug(f"Updated operational state for carrier {carrier_name}")

        except Exception as e:
            logger.error(f"Failed to update carrier operational state: {e}")

    def _send_carrier_state_notification(self, carrier_name: str, carrier_type: str, new_state: str):
        """
        Send carrier state change notification.

        Args:
            carrier_name: Name of the carrier
            carrier_type: "tx" or "rx"
            new_state: New active state
        """
        try:
            event_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

            notification_data = {
                "carrier-name": carrier_name,
                "carrier-type": f"{carrier_type}-array-carrier",
                "new-state": new_state,
                "event-time": event_time
            }

            logger.info(f"Sending carrier state change notification: {carrier_name} {carrier_type}={new_state}")

            # Send notification (path may vary based on YANG module structure)
            try:
                self.netconf.running.notification_send(
                    "/o-ran-uplane-conf:carrier-state-change",
                    notification_data
                )
            except Exception as e:
                # Notification path might not exist in all YANG versions
                logger.debug(f"Could not send notification: {e}")

        except Exception as e:
            logger.error(f"Failed to send carrier state notification: {e}")

    def get_carrier_state(self, carrier_name: str) -> dict:
        """Get current state of a carrier."""
        with self.lock:
            return self.carriers.get(carrier_name, {
                "tx_active": "INACTIVE",
                "rx_active": "INACTIVE",
                "tx_state": "DISABLED",
                "rx_state": "DISABLED"
            })

    # ========== Antenna Mask Control (3.1.14.4) ==========

    def _handle_antenna_mask_change(self, change, change_str: str):
        """
        Handle antenna mask configuration changes.

        Args:
            change: Configuration change event
            change_str: String representation of change
        """
        try:
            # Extract array name
            array_name = "array-1"  # Default
            match = re.search(r"array-name['\"]?\s*[:=]\s*['\"]?([^'\">\s]+)", change_str)
            if match:
                array_name = match.group(1)

            # Determine array type (tx or rx)
            array_type = "tx" if "tx-array" in change_str else "rx"

            # Extract bitmask value or index
            bitmask = None
            bitmask_index = None

            match = re.search(r"antenna-bitmask['\"]?\s*[:=]\s*['\"]?([^'\">\s]+)", change_str)
            if match:
                bitmask = match.group(1)

            match = re.search(r"antenna-bitmask-index['\"]?\s*[:=]\s*(\d+)", change_str)
            if match:
                bitmask_index = int(match.group(1))

            logger.info(f"Antenna mask change: {array_type}-array {array_name}, bitmask={bitmask}, index={bitmask_index}")

            # Store the configuration
            with self.lock:
                self.antenna_masks[f"{array_type}_{array_name}"] = {
                    "bitmask": bitmask,
                    "bitmask_index": bitmask_index
                }

            # Send notification
            self._send_antenna_mask_notification(array_name, array_type, bitmask, bitmask_index)

        except Exception as e:
            logger.error(f"Error handling antenna mask change: {e}")

    def _send_antenna_mask_notification(self, array_name: str, array_type: str, bitmask: str, bitmask_index: int):
        """
        Send mplane-trx-control-ant-mask-update notification.

        Args:
            array_name: Name of the array
            array_type: "tx" or "rx"
            bitmask: Antenna bitmask value
            bitmask_index: Antenna bitmask index
        """
        try:
            event_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

            notification_data = {
                "array-name": array_name,
                "array-type": f"{array_type}-array",
                "event-time": event_time
            }
            if bitmask:
                notification_data["antenna-bitmask"] = bitmask
            if bitmask_index is not None:
                notification_data["antenna-bitmask-index"] = bitmask_index

            logger.info(f"Sending antenna mask update notification: {array_name}")

            try:
                self.netconf.running.notification_send(
                    "/o-ran-uplane-conf:mplane-trx-control-ant-mask-update",
                    notification_data
                )
            except Exception as e:
                logger.debug(f"Could not send notification: {e}")

        except Exception as e:
            logger.error(f"Failed to send antenna mask notification: {e}")

    # ========== Energy Sharing Groups (3.1.14.6) ==========

    def _handle_energy_sharing_group_change(self, change, change_str: str):
        """
        Handle energy-sharing-groups-disabled configuration changes.

        Args:
            change: Configuration change event
            change_str: String representation of change
        """
        try:
            # Check if this is an add or remove operation
            is_delete = "delete" in change_str.lower() or "->None" in change_str

            # Extract group ID
            match = re.search(r"energy-sharing-groups-disabled['\"]?\s*[:=\[]?\s*(\d+)", change_str)
            if match:
                group_id = int(match.group(1))
            else:
                # Try alternative patterns
                match = re.search(r"(\d+)", change_str)
                group_id = int(match.group(1)) if match else 4  # Default from spec

            with self.lock:
                if is_delete:
                    # Remove from disabled list (re-enable)
                    if group_id in self.energy_sharing_groups_disabled:
                        self.energy_sharing_groups_disabled.remove(group_id)
                        logger.info(f"Energy sharing group {group_id} re-enabled")
                        # Send wakeup notification
                        self._send_data_layer_wakeup_notification(group_id)
                else:
                    # Add to disabled list (disable)
                    if group_id not in self.energy_sharing_groups_disabled:
                        self.energy_sharing_groups_disabled.append(group_id)
                        logger.info(f"Energy sharing group {group_id} disabled")

        except Exception as e:
            logger.error(f"Error handling energy sharing group change: {e}")

    def _send_data_layer_wakeup_notification(self, group_id: int):
        """
        Send data-layer-control-wakeup-notification.

        Args:
            group_id: Energy sharing group ID that is waking up
        """
        try:
            event_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

            notification_data = {
                "energy-sharing-group": group_id,
                "event-time": event_time
            }

            logger.info(f"Sending data layer wakeup notification: group {group_id}")

            try:
                self.netconf.running.notification_send(
                    "/o-ran-uplane-conf:data-layer-control-wakeup-notification",
                    notification_data
                )
            except Exception as e:
                logger.debug(f"Could not send notification: {e}")

        except Exception as e:
            logger.error(f"Failed to send data layer wakeup notification: {e}")
