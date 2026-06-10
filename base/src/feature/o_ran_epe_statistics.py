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
            self.netconf.running.subscribe_module_change(
                "o-ran-performance-management",
                None,
                self._handle_config_change,
            )
            logger.info("Successfully subscribed to o-ran-performance-management module changes")
            self._initialise_from_running_datastore()
        except Exception as e:
            logger.error(f"Failed to start EPE statistics feature: {e}")

    def _initialise_from_running_datastore(self):
        """
        Seed measurement-interval and active state from the current running datastore.

        Without this, a pynts restart loses track of an already-configured interval:
        sysrepo only fires a change event when the value differs from the previous
        one, so a subsequent edit-config with the same interval would be a silent
        no-op and the notification loop would keep running at the hard-coded default.
        """
        try:
            raw = self.netconf.running.get_data(
                "/o-ran-performance-management:performance-measurement-objects/epe-measurement-interval"
            )
            # get_data returns the subtree: {'performance-measurement-objects':
            #   {'epe-measurement-interval': 5}}
            interval_val = None
            if isinstance(raw, dict):
                pmo = raw.get("performance-measurement-objects") or {}
                interval_val = pmo.get("epe-measurement-interval")
            elif raw is not None:
                interval_val = raw
            if interval_val is not None:
                with self.lock:
                    self.measurement_interval = int(interval_val)
                    self.notification_interval = self.measurement_interval
                logger.info(
                    f"Initialised measurement-interval from running datastore: "
                    f"{self.measurement_interval}s"
                )
        except Exception as e:
            logger.debug(f"Running datastore lookup for measurement-interval failed: {e}")

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
        """
        Handle measurement-interval leaf changes.

        Reads only the value after '->' so an 'OLD -> NEW' transition is parsed
        as NEW; previously the regex captured the OLD value.
        """
        try:
            if "epe-measurement-interval" not in change_str:
                return
            if "->" not in change_str:
                return
            import re
            new_val_raw = change_str.rsplit("->", 1)[1].strip()
            match = re.search(r"(\d+)", new_val_raw)
            if not match:
                return
            interval = int(match.group(1))
            if interval <= 0:
                return
            with self.lock:
                self.measurement_interval = interval
                self.notification_interval = interval
            logger.info(f"EPE measurement interval set to {interval} seconds")
        except Exception as e:
            logger.error(f"Error handling interval change: {e}")

    def _handle_active_change(self, change, change_str: str):
        """
        Handle activation/deactivation of EPE measurement.

        The sysrepo change string takes the form ``<xpath>: 'OLD' -> NEW`` where
        NEW is a Python literal (``True``/``False``/``None``). We parse only the
        post-``->`` side so that a transition like ``'false' -> True`` is not
        mis-classified as a deactivate just because "false" appears on the left.
        """
        try:
            # Only inspect changes on the 'active' leaf itself; skip the parent list
            # change events whose change_str mentions 'active' as part of a longer path.
            if "/active" not in change_str and not change_str.rstrip().endswith("/active"):
                # Fallback: still allow a pure "active" leaf change
                if "active:" not in change_str:
                    return

            # Extract the part after the last '->' and normalise
            if "->" not in change_str:
                return
            new_val_raw = change_str.rsplit("->", 1)[1].strip()
            # Strip surrounding quotes if present
            new_val = new_val_raw.strip("'\"")
            new_lower = new_val.lower()

            if new_lower in ("true", "1"):
                logger.info("EPE active leaf transitioned to True → activating")
                self._activate_epe_measurement()
            elif new_lower in ("false", "0", "none", "null"):
                logger.info(f"EPE active leaf transitioned to {new_val} → deactivating")
                self._deactivate_epe_measurement()
            else:
                logger.debug(f"Ignoring unrecognised active-leaf transition: {new_val!r}")

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
        """
        Send a ``measurement-result-stats`` notification per o-ran-performance-management
        YANG (notification ``measurement-result-stats`` uses the ``measurement-notification``
        grouping which carries ``epe-statistics[measurement-object]``).

        The numeric power values are synthetic; what matters is that the notification
        structure + periodicity match the WG4 spec 3.1.14.3 expectations.
        """
        try:
            now_ts = self._iso_zulu(datetime.now(timezone.utc))
            start_ts = self._iso_zulu(
                datetime.now(timezone.utc).replace(microsecond=0)
            )
            avg_power = self._generate_power_measurement()
            min_power = round(avg_power - self.power_variation, 4)
            max_power = round(avg_power + self.power_variation, 4)

            notification_data = {
                "epe-statistics": [
                    {
                        "measurement-object": self.measurement_object,
                        "start-time": start_ts,
                        "end-time": now_ts,
                        "epe-measurement-resultv2": [
                            {
                                "object-unit-id": self.object_unit,
                                "min": f"{min_power:.4f}",
                                "max": f"{max_power:.4f}",
                                "average": f"{avg_power:.4f}",
                            }
                        ],
                    }
                ]
            }

            logger.info(
                f"Sending EPE notification: POWER avg={avg_power}W "
                f"(min={min_power}/max={max_power})"
            )
            self.netconf.running.notification_send(
                "/o-ran-performance-management:measurement-result-stats",
                notification_data,
            )
        except Exception as e:
            logger.error(f"Failed to send EPE notification: {e}")

    @staticmethod
    def _iso_zulu(dt) -> str:
        """Render a UTC datetime as YANG yang-types:date-and-time (milliseconds, Z suffix)."""
        return dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

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
