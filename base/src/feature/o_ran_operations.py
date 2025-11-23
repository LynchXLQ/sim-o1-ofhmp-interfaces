

from util.logging import get_pynts_logger
import threading
import os
import signal
from core.netconf import Netconf
from util.threading import stop_event

logger = get_pynts_logger("feature-o-ran-operations")

class ORanOperationsFeature:
    """
    O-RAN Operations feature implementation.

    Implements the o-ran-operations YANG module RPCs:
    - reset: Management plane triggered restart of the radio unit
    """

    def __init__(self) -> None:
        self.netconf = Netconf()

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
