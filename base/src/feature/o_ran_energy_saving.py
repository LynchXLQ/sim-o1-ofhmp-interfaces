"""
O-RAN Energy Saving Feature Implementation.

Implements the energy saving functionality from o-ran-hardware YANG module:
- energy-saving-enabled configuration handling
- power-state operational data updates
- ENERGYSAVING feature support

Reference: O-RAN.WG4.MP.0-v13.00 and O-RAN.WG4.TS.CONF.0-R004-v13.00 Section 3.1.14.1
"""

from util.logging import get_pynts_logger
import threading
from core.netconf import Netconf, Datastore
from util.threading import stop_event

logger = get_pynts_logger("feature-o-ran-energy-saving")


class ORanEnergySavingFeature:
    """
    O-RAN Energy Saving feature implementation.

    Handles energy-saving-enabled configuration and updates power-state
    operational data accordingly.
    """

    def __init__(self) -> None:
        self.netconf = Netconf()
        self.energy_saving_enabled = False
        self.power_state = "AWAKE"  # Default state
        self.lock = threading.Lock()

    def start(self) -> None:
        """Start the energy saving feature by subscribing to config changes."""
        logger.info("Starting O-RAN energy saving feature")
        try:
            # Initialize operational state
            self._initialize_operational_state()

            # Subscribe to energy-saving-enabled configuration changes
            self.netconf.running.subscribe_module_change(
                "o-ran-hardware",
                None,  # xpath filter (None = all changes)
                self._handle_config_change
            )
            logger.info("Successfully subscribed to o-ran-hardware module changes")

        except Exception as e:
            logger.error(f"Failed to start energy saving feature: {e}")

    def _initialize_operational_state(self):
        """Initialize energy saving operational state."""
        try:
            # Set default power-state to AWAKE
            self._update_power_state("AWAKE")
            logger.info("Initialized energy saving operational state")

        except Exception as e:
            logger.error(f"Failed to initialize operational state: {e}")

    def _handle_config_change(self, event, request_id, changes, private_data):
        """
        Handle configuration changes for o-ran-hardware module.

        Watches for changes to energy-saving-enabled leaf and updates
        power-state accordingly.
        """
        logger.info(f"Config change event: {event}")

        try:
            for change in changes:
                logger.debug(f"Change: {change}")

                # Check if this is an energy-saving-enabled change
                if "energy-saving-enabled" in str(change):
                    # Extract the new value
                    new_value = self._extract_energy_saving_value(change)

                    with self.lock:
                        old_value = self.energy_saving_enabled
                        self.energy_saving_enabled = new_value

                    if new_value != old_value:
                        logger.info(f"Energy saving enabled changed: {old_value} -> {new_value}")

                        # Update power-state based on energy-saving-enabled
                        if new_value:
                            self._update_power_state("SLEEPING")
                        else:
                            self._update_power_state("AWAKE")

        except Exception as e:
            logger.error(f"Error handling config change: {e}")

    def _extract_energy_saving_value(self, change):
        """Extract energy-saving-enabled value from change event."""
        try:
            # Parse the change to get the value
            # Change format varies by sysrepo version
            if hasattr(change, 'new_value'):
                return bool(change.new_value)
            elif hasattr(change, 'value'):
                return bool(change.value)
            elif isinstance(change, dict):
                return change.get('energy-saving-enabled', False)
            else:
                # Try to parse from string representation
                change_str = str(change).lower()
                return 'true' in change_str

        except Exception as e:
            logger.error(f"Error extracting energy saving value: {e}")
            return False

    def _update_power_state(self, state: str):
        """
        Update power-state operational data.

        Args:
            state: Power state value (SLEEPING, AWAKE)
        """
        try:
            with self.lock:
                self.power_state = state

            # Update operational data in hardware component
            # The power-state is under /ietf-hardware:hardware/component/o-ran-hardware:power-state
            operational_data = {
                "hardware": {
                    "component": [
                        {
                            "name": "O-RAN-RADIO",
                            "class": "iana-hardware:module",
                            "o-ran-hardware:power-state": state
                        }
                    ]
                }
            }

            self.netconf.set_data(Datastore.OPERATIONAL, "ietf-hardware", operational_data)
            logger.info(f"Updated power-state to {state}")

        except Exception as e:
            logger.error(f"Failed to update power-state: {e}")

    def get_power_state(self) -> str:
        """Get current power state."""
        with self.lock:
            return self.power_state

    def is_energy_saving_enabled(self) -> bool:
        """Check if energy saving is enabled."""
        with self.lock:
            return self.energy_saving_enabled
