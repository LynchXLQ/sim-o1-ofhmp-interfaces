"""
O-RAN EPE Statistics Feature Implementation.

Implements the Energy, Power and Environmental (EPE) statistics functionality
from o-ran-performance-management YANG module:
- EPE measurement activation/deactivation via edit-config
- NETCONF subscription to epe-statistics measurement results
- Periodic NETCONF notifications with POWER measurements

Reference: O-RAN.WG4.MP.0-v13.00 and O-RAN.WG4.TS.CONF.0-R004-v13.00 Section 3.1.14.3
"""

from util.logging import get_pynts_logger
import threading
import random
from datetime import datetime, timezone
from core.netconf import Netconf, Datastore
from util.threading import stop_event

logger = get_pynts_logger("feature-o-ran-epe-statistics")


class ORanEpeStatisticsFeature:
    """
    O-RAN EPE Statistics feature implementation.

    Handles EPE measurement activation/deactivation and sends periodic
    NETCONF notifications with power measurement data.
    """

    def __init__(self) -> None:
        self.netconf = Netconf()
        self.lock = threading.Lock()

        # EPE measurement configuration
        self.epe_active = False
        self.measurement_interval = 60  # seconds (default)
        self.notification_interval = 60  # seconds (default)
        self.measurement_object = "POWER"
        self.report_info = "AVERAGE"
        self.object_unit = "O-RAN-RADIO"

        # Notification thread
        self.notification_thread = None
        self.notification_stop_event = threading.Event()

        # Simulated power values (Watts)
        self.base_power = 50.0
        self.power_variation = 5.0

    def start(self) -> None:
        """Start the EPE statistics feature by subscribing to config changes."""
        logger.info("Starting O-RAN EPE statistics feature")
        try:
            # Subscribe to performance-management module changes
            self.netconf.running.subscribe_module_change(
                "o-ran-performance-management",
                None,  # xpath filter (None = all changes)
                self._handle_config_change
            )
            logger.info("Successfully subscribed to o-ran-performance-management module changes")

        except Exception as e:
            logger.error(f"Failed to start EPE statistics feature: {e}")

    def _handle_config_change(self, event, request_id, changes, private_data):
        """
        Handle configuration changes for o-ran-performance-management module.

        Watches for changes to:
        - epe-statistics measurement activation/deactivation
        - measurement-interval configuration
        """
        logger.info(f"EPE statistics config change event: {event}")

        try:
            for change in changes:
                logger.debug(f"Change: {change}")
                change_str = str(change)

                # Check for EPE statistics activation/deactivation
                if "epe-statistics" in change_str or "measurement-group" in change_str:
                    self._handle_epe_config_change(change, change_str)

                # Check for measurement interval change
                if "measurement-interval" in change_str:
                    self._handle_interval_change(change, change_str)

                # Check for active state change
                if "active" in change_str.lower():
                    self._handle_active_change(change, change_str)

        except Exception as e:
            logger.error(f"Error handling config change: {e}")

    def _handle_epe_config_change(self, change, change_str: str):
        """Handle EPE configuration changes."""
        try:
            # Check if this is setting up EPE measurement
            if "POWER" in change_str.upper():
                self.measurement_object = "POWER"
                logger.info("EPE measurement object set to POWER")

            if "AVERAGE" in change_str.upper() or "average" in change_str:
                self.report_info = "AVERAGE"
                logger.info("EPE report info set to AVERAGE")

            if "O-RAN-RADIO" in change_str or "o-ran-radio" in change_str.lower():
                self.object_unit = "O-RAN-RADIO"
                logger.info("EPE object unit set to O-RAN-RADIO")

        except Exception as e:
            logger.error(f"Error handling EPE config change: {e}")

    def _handle_interval_change(self, change, change_str: str):
        """Handle measurement interval changes."""
        try:
            import re

            # Try to extract interval value
            match = re.search(r"measurement-interval['\"]?\s*[:=]?\s*(\d+)", change_str)
            if match:
                interval = int(match.group(1))
                with self.lock:
                    self.measurement_interval = interval
                    self.notification_interval = interval
                logger.info(f"EPE measurement interval set to {interval} seconds")

        except Exception as e:
            logger.error(f"Error handling interval change: {e}")

    def _handle_active_change(self, change, change_str: str):
        """Handle activation/deactivation of EPE measurement."""
        try:
            # Determine if activating or deactivating
            is_activate = "true" in change_str.lower() or "->True" in change_str
            is_deactivate = "false" in change_str.lower() or "->False" in change_str or "delete" in change_str.lower()

            # Also check for None transitions
            if "->None" in change_str:
                is_deactivate = True
                is_activate = False

            if is_activate and not is_deactivate:
                self._activate_epe_measurement()
            elif is_deactivate:
                self._deactivate_epe_measurement()

        except Exception as e:
            logger.error(f"Error handling active change: {e}")

    def _activate_epe_measurement(self):
        """Activate EPE measurement and start sending notifications."""
        with self.lock:
            if self.epe_active:
                logger.info("EPE measurement already active")
                return

            self.epe_active = True
            self.notification_stop_event.clear()

        logger.info(f"Activating EPE measurement: interval={self.measurement_interval}s, object={self.measurement_object}")

        # Start notification thread
        self.notification_thread = threading.Thread(
            target=self._notification_loop,
            name="epe-statistics-notifications"
        )
        self.notification_thread.daemon = True
        self.notification_thread.start()

    def _deactivate_epe_measurement(self):
        """Deactivate EPE measurement and stop sending notifications."""
        with self.lock:
            if not self.epe_active:
                logger.info("EPE measurement already inactive")
                return

            self.epe_active = False
            self.notification_stop_event.set()

        logger.info("Deactivating EPE measurement")

        # Wait for notification thread to stop
        if self.notification_thread and self.notification_thread.is_alive():
            self.notification_thread.join(timeout=5)

    def _notification_loop(self):
        """Send periodic EPE statistics notifications."""
        logger.info("EPE notification loop started")

        import time

        while not stop_event.is_set() and not self.notification_stop_event.is_set():
            with self.lock:
                if not self.epe_active:
                    break
                interval = self.measurement_interval

            # Send notification
            self._send_epe_notification()

            # Wait for next interval (check every second for stop event)
            for _ in range(interval):
                if stop_event.is_set() or self.notification_stop_event.is_set():
                    break
                time.sleep(1)

        logger.info("EPE notification loop stopped")

    def _send_epe_notification(self):
        """Send EPE measurement result notification."""
        try:
            event_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

            # Generate simulated power measurement
            power_value = self._generate_power_measurement()

            notification_data = {
                "measurement-result-statistics": {
                    "epe-statistics": {
                        "measurement-object": self.measurement_object,
                        "report-info": self.report_info,
                        "object-unit": self.object_unit,
                        "measurement-result": {
                            "value": str(power_value),
                            "unit": "watts"
                        },
                        "event-time": event_time
                    }
                }
            }

            logger.info(f"Sending EPE notification: POWER={power_value}W")

            try:
                self.netconf.running.notification_send(
                    "/o-ran-performance-management:measurement-result-statistics",
                    notification_data
                )
            except Exception as e:
                logger.debug(f"Could not send notification: {e}")

        except Exception as e:
            logger.error(f"Failed to send EPE notification: {e}")

    def _generate_power_measurement(self) -> float:
        """
        Generate simulated power measurement.

        Returns:
            Simulated power value in Watts
        """
        with self.lock:
            # Add some random variation to base power
            variation = random.uniform(-self.power_variation, self.power_variation)
            power = self.base_power + variation
            return round(power, 2)

    def is_epe_active(self) -> bool:
        """Check if EPE measurement is active."""
        with self.lock:
            return self.epe_active

    def get_measurement_config(self) -> dict:
        """Get current measurement configuration."""
        with self.lock:
            return {
                "active": self.epe_active,
                "measurement_interval": self.measurement_interval,
                "measurement_object": self.measurement_object,
                "report_info": self.report_info,
                "object_unit": self.object_unit
            }
