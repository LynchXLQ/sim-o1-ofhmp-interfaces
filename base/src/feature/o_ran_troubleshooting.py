

from util.logging import get_pynts_logger
import threading
import time
from datetime import datetime
from core.netconf import Netconf

logger = get_pynts_logger("feature-o-ran-troubleshooting")

class ORanTroubleshootingFeature:
    """
    O-RAN Troubleshooting feature implementation.

    Implements the o-ran-troubleshooting YANG module (urn:o-ran:troubleshooting:1.0):
    - start-troubleshooting-logs: Start generating troubleshooting logs
    - stop-troubleshooting-logs: Stop generating troubleshooting logs

    Per O-RAN WG4 spec 3.1.12.1, troubleshooting logs contain alarm information.
    """

    def __init__(self) -> None:
        self.netconf = Netconf()
        self.log_generation_active = False

    def start(self) -> None:
        """Start the O-RAN troubleshooting feature by subscribing to RPCs."""
        logger.info("Starting O-RAN troubleshooting feature")
        try:
            # Subscribe to start-troubleshooting-logs RPC
            self.netconf.running.subscribe_rpc_call(
                "/o-ran-troubleshooting:start-troubleshooting-logs",
                self._handle_start_troubleshooting_logs
            )

            # Subscribe to stop-troubleshooting-logs RPC
            self.netconf.running.subscribe_rpc_call(
                "/o-ran-troubleshooting:stop-troubleshooting-logs",
                self._handle_stop_troubleshooting_logs
            )

            logger.info("Successfully subscribed to troubleshooting RPCs")
        except Exception as e:
            logger.error(f"Failed to start O-RAN troubleshooting feature: {e}")

    def _handle_start_troubleshooting_logs(self, rpc_path, input_params, event, private_data):
        """
        Handle start-troubleshooting-logs RPC.

        Per O-RAN spec 3.1.12.1 step 1:
        - Receive start-troubleshooting-logs RPC
        - Return SUCCESS status
        - Generate troubleshooting log file
        - Send troubleshooting-log-generated notification

        Args:
            rpc_path: XPath to the RPC
            input_params: Input parameters (none for this RPC)
            event: Sysrepo event type
            private_data: User private data

        Returns:
            Dict with status=SUCCESS
        """
        logger.info("Received start-troubleshooting-logs RPC")

        try:
            self.log_generation_active = True

            # Generate log file in background thread
            def generate_log():
                time.sleep(2)  # Simulate log generation delay

                if self.log_generation_active:
                    log_file_name = f"o-ran/log/troubleshooting_{datetime.now().strftime('%Y%m%d_%H%M%S')}.gz"
                    logger.info(f"Generated troubleshooting log: {log_file_name}")

                    # Send troubleshooting-log-generated notification
                    self._send_log_generated_notification(log_file_name)

            log_thread = threading.Thread(target=generate_log, name="troubleshooting-log-generator")
            log_thread.daemon = True
            log_thread.start()

            # Return SUCCESS per O-RAN spec
            return {"status": "SUCCESS"}

        except Exception as e:
            logger.error(f"Error in start-troubleshooting-logs: {e}")
            return {"status": "FAILURE"}

    def _handle_stop_troubleshooting_logs(self, rpc_path, input_params, event, private_data):
        """
        Handle stop-troubleshooting-logs RPC.

        Args:
            rpc_path: XPath to the RPC
            input_params: Input parameters (none for this RPC)
            event: Sysrepo event type
            private_data: User private data

        Returns:
            Dict with status=SUCCESS
        """
        logger.info("Received stop-troubleshooting-logs RPC")

        try:
            self.log_generation_active = False
            return {"status": "SUCCESS"}
        except Exception as e:
            logger.error(f"Error in stop-troubleshooting-logs: {e}")
            return {"status": "FAILURE"}

    def _send_log_generated_notification(self, log_file_name: str):
        """
        Send troubleshooting-log-generated notification.

        Per O-RAN spec 3.1.12.1 step 2:
        Notification contains log-file-name element.

        Args:
            log_file_name: Path to generated log file
        """
        try:
            # log-file-name is a leaf-list in YANG, so it must be a Python list
            notification_data = {
                "log-file-name": [log_file_name]
            }

            xpath = "/o-ran-troubleshooting:troubleshooting-log-generated"
            logger.info(f"Sending troubleshooting-log-generated notification for {log_file_name}")
            self.netconf.running.notification_send(xpath, notification_data)

        except Exception as e:
            logger.error(f"Failed to send troubleshooting-log-generated notification: {e}")
