"""
O-RAN Operations Feature Implementation.

Implements the o-ran-operations YANG module RPCs:
- reset: Management plane triggered restart of the radio unit
- deep-hibernate: Put the O-RU into deep hibernation state for energy saving

Reference: O-RAN.WG4.MP.0-v13.00 and O-RAN.WG4.TS.CONF.0-R004-v13.00 Section 3.1.14.5
"""

from util.logging import get_pynts_logger
import threading
import os
import signal
from datetime import datetime, timezone
from core.netconf import Netconf
from util.threading import stop_event

logger = get_pynts_logger("feature-o-ran-operations")


class ORanOperationsFeature:
    """
    O-RAN Operations feature implementation.

    Implements the o-ran-operations YANG module RPCs:
    - reset: Management plane triggered restart of the radio unit
    - deep-hibernate: Put the O-RU into deep hibernation state
    """

    def __init__(self) -> None:
        self.netconf = Netconf()
        self.hibernate_timer = None
        self.hibernate_time_remaining = 0
        self.lock = threading.Lock()

    def start(self) -> None:
        """Start the O-RAN operations feature by subscribing to RPCs."""
        logger.info("Starting O-RAN operations feature")
        try:
            # Subscribe to reset RPC
            self.netconf.running.subscribe_rpc_call(
                "/o-ran-operations:reset",
                self._handle_reset_rpc
            )
            logger.info("Successfully subscribed to reset RPC")

            # Subscribe to deep-hibernate RPC (if-feature DEEP-HIBERNATE)
            self.netconf.running.subscribe_rpc_call(
                "/o-ran-operations:deep-hibernate",
                self._handle_deep_hibernate_rpc
            )
            logger.info("Successfully subscribed to deep-hibernate RPC")

        except Exception as e:
            logger.error(f"Failed to start O-RAN operations feature: {e}")

    def _handle_reset_rpc(self, rpc_path, input_params, event, private_data):
        """
        Handle incoming reset RPC calls.

        According to O-RAN.WG4.MP.0-v13.00 specification section 3.1.7.2:
        "Management plane triggered restart of the radio unit.
        A server SHOULD send an rpc reply to the client before restarting the system."

        Args:
            rpc_path: XPath to the RPC (/o-ran-operations:reset)
            input_params: Input parameters (None for reset RPC - no inputs defined)
            event: Sysrepo event type
            private_data: User private data

        Returns:
            Empty dict for successful acknowledgment (no output parameters defined)
        """
        logger.info("Received reset RPC - management plane triggered restart")

        try:
            # Schedule restart in background thread to allow RPC reply to be sent first
            # This follows the YANG description: "send rpc reply before restarting"
            def delayed_restart():
                """Execute system restart after a short delay."""
                import time

                # Wait 1 second to ensure RPC reply is sent
                logger.info("Waiting 1 second before triggering restart...")
                time.sleep(1)

                logger.warning("Executing M-Plane triggered system restart...")

                # Set stop event to signal graceful shutdown
                stop_event.set()

                # Give graceful shutdown 2 seconds to complete
                time.sleep(2)

                # Force termination if graceful shutdown didn't work
                logger.warning("Sending SIGTERM to terminate process")
                os.kill(os.getpid(), signal.SIGTERM)

                # Final fallback after 1 more second
                time.sleep(1)
                logger.error("Process still running after SIGTERM, forcing exit")
                os._exit(0)

            # Start restart thread (daemon thread won't block process exit)
            restart_thread = threading.Thread(target=delayed_restart, name="reset-rpc-handler")
            restart_thread.daemon = True
            restart_thread.start()

            logger.info("Reset RPC acknowledged - restart scheduled in background")

            # Return empty dict for success (reset RPC has no output parameters)
            # NETCONF server will send <rpc-reply><ok/></rpc-reply>
            return {}

        except Exception as e:
            logger.error(f"Error handling reset RPC: {e}")
            # Return empty dict even on error to avoid breaking NETCONF session
            # Log the error but still trigger restart
            return {}

    # ========== Deep Hibernate RPC (3.1.14.5) ==========

    def _handle_deep_hibernate_rpc(self, rpc_path, input_params, event, private_data):
        """
        Handle incoming deep-hibernate RPC calls.

        According to O-RAN.WG4.TS.CONF.0-R004-v13.00 Section 3.1.14.5:
        The TER invokes deep-hibernate RPC to put the O-RU into hibernation state.
        The O-RU shall send deep-hibernate-activated notification when hibernation starts.
        After hibernate-time expires, O-RU restarts with restart-cause = DEEP-HIBERNATE-RESTART.

        Args:
            rpc_path: XPath to the RPC (/o-ran-operations:deep-hibernate)
            input_params: Input parameters containing hibernate-time
            event: Sysrepo event type
            private_data: User private data

        Returns:
            Dict with status: STARTED or FAILED
        """
        logger.info(f"Received deep-hibernate RPC: {input_params}")

        try:
            # Extract hibernate-time from input (mandatory parameter)
            hibernate_time = None

            if input_params:
                if isinstance(input_params, dict):
                    # Try various possible key formats
                    hibernate_time = input_params.get("hibernate-time")
                    if hibernate_time is None:
                        hibernate_time = input_params.get("o-ran-operations:hibernate-time")
                    if hibernate_time is None:
                        # Try to find it in nested structure
                        for key, value in input_params.items():
                            if "hibernate" in key.lower():
                                if isinstance(value, dict):
                                    hibernate_time = value.get("hibernate-time", value.get("o-ran-operations:hibernate-time"))
                                else:
                                    hibernate_time = value
                                break

            if hibernate_time is None:
                logger.error("Missing mandatory hibernate-time parameter")
                return {"status": "FAILED"}

            # Convert to integer (minutes)
            hibernate_time = int(hibernate_time)
            logger.info(f"Deep hibernate requested for {hibernate_time} minutes")

            # Cancel any existing hibernate timer
            with self.lock:
                if self.hibernate_timer:
                    self.hibernate_timer.cancel()
                    self.hibernate_timer = None

                self.hibernate_time_remaining = hibernate_time

            # Schedule hibernation (simulated - in real O-RU would enter low-power state)
            def execute_hibernation():
                """Execute the hibernation sequence."""
                import time

                logger.info(f"Starting deep hibernation for {hibernate_time} minutes")

                # Send deep-hibernate-activated notification
                self._send_deep_hibernate_activated_notification(hibernate_time)

                # In a real O-RU, this would enter a low-power state
                # For simulation, we just wait for the hibernate time
                # Using shorter time for testing (seconds instead of minutes)
                wait_time = hibernate_time * 60  # Convert minutes to seconds

                # For testing, cap at 60 seconds to avoid long waits
                if wait_time > 60:
                    logger.info(f"Capping hibernate wait time to 60 seconds for testing")
                    wait_time = 60

                logger.info(f"Hibernating for {wait_time} seconds...")
                time.sleep(wait_time)

                # After hibernate time expires, restart with DEEP-HIBERNATE-RESTART cause
                logger.info("Hibernate time expired - triggering restart with DEEP-HIBERNATE-RESTART cause")

                # Update restart-cause in operational data (if possible)
                try:
                    self._update_restart_cause("DEEP-HIBERNATE-RESTART")
                except Exception as e:
                    logger.debug(f"Could not update restart cause: {e}")

                # Trigger restart
                stop_event.set()
                time.sleep(2)
                os.kill(os.getpid(), signal.SIGTERM)

            # Start hibernation in background thread
            hibernate_thread = threading.Thread(
                target=execute_hibernation,
                name="deep-hibernate-handler"
            )
            hibernate_thread.daemon = True
            hibernate_thread.start()

            with self.lock:
                self.hibernate_timer = hibernate_thread

            logger.info("Deep hibernate RPC acknowledged - hibernation started")
            return {"status": "STARTED"}

        except ValueError as e:
            logger.error(f"Invalid hibernate-time value: {e}")
            return {"status": "FAILED"}
        except Exception as e:
            logger.error(f"Error handling deep-hibernate RPC: {e}")
            return {"status": "FAILED"}

    def _send_deep_hibernate_activated_notification(self, hibernate_time: int):
        """
        Send deep-hibernate-activated notification.

        Args:
            hibernate_time: Configured hibernate time in minutes
        """
        try:
            event_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

            notification_data = {
                "hibernate-time": hibernate_time,
                "event-time": event_time
            }

            logger.info(f"Sending deep-hibernate-activated notification: {hibernate_time} minutes")

            try:
                self.netconf.running.notification_send(
                    "/o-ran-operations:deep-hibernate-activated",
                    notification_data
                )
            except Exception as e:
                logger.debug(f"Could not send notification: {e}")

        except Exception as e:
            logger.error(f"Failed to send deep-hibernate-activated notification: {e}")

    def _update_restart_cause(self, cause: str):
        """
        Update restart-cause in operational data.

        Args:
            cause: Restart cause value (e.g., DEEP-HIBERNATE-RESTART)
        """
        try:
            from core.netconf import Datastore

            operational_data = {
                "operational-info": {
                    "restart-cause": cause
                }
            }

            self.netconf.set_data(Datastore.OPERATIONAL, "o-ran-operations", operational_data)
            logger.info(f"Updated restart-cause to {cause}")

        except Exception as e:
            logger.debug(f"Could not update restart-cause: {e}")
