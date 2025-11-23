

from util.logging import get_pynts_logger
import threading
import time
from datetime import datetime
from core.netconf import Netconf

logger = get_pynts_logger("feature-o-ran-trace")

class ORanTraceFeature:
    """
    O-RAN Trace feature implementation.

    Implements the o-ran-trace YANG module (urn:o-ran:trace:1.0):
    - start-trace-logs: Start generating trace logs
    - stop-trace-logs: Stop generating trace logs

    Per O-RAN WG4 spec 3.1.12.2:
    - First notification has is-notification-last=false (step 2)
    - Final notification has is-notification-last=true (step 6)
    """

    def __init__(self) -> None:
        self.netconf = Netconf()
        self.trace_active = False
        self.trace_thread = None

    def start(self) -> None:
        """Start the O-RAN trace feature by subscribing to RPCs."""
        logger.info("Starting O-RAN trace feature")
        try:
            # Subscribe to start-trace-logs RPC
            self.netconf.running.subscribe_rpc_call(
                "/o-ran-trace:start-trace-logs",
                self._handle_start_trace_logs
            )

            # Subscribe to stop-trace-logs RPC
            self.netconf.running.subscribe_rpc_call(
                "/o-ran-trace:stop-trace-logs",
                self._handle_stop_trace_logs
            )

            logger.info("Successfully subscribed to trace RPCs")
        except Exception as e:
            logger.error(f"Failed to start O-RAN trace feature: {e}")

    def _handle_start_trace_logs(self, rpc_path, input_params, event, private_data):
        """
        Handle start-trace-logs RPC.

        Per O-RAN spec 3.1.12.2 step 1:
        - Receive start-trace-logs RPC
        - Return SUCCESS status
        - Generate first batch of trace logs
        - Send notification with is-notification-last=false (step 2)

        Args:
            rpc_path: XPath to the RPC
            input_params: Input parameters (none for this RPC)
            event: Sysrepo event type
            private_data: User private data

        Returns:
            Dict with status=SUCCESS
        """
        logger.info("Received start-trace-logs RPC")

        try:
            self.trace_active = True

            # Generate trace logs in background thread
            def generate_trace_logs():
                # Generate first batch (step 2)
                time.sleep(2)  # Simulate log generation delay

                if self.trace_active:
                    log_file_name = f"o-ran/log/trace_batch1_{datetime.now().strftime('%Y%m%d_%H%M%S')}.gz"
                    logger.info(f"Generated first trace log batch: {log_file_name}")

                    # Send first notification with is-notification-last=false
                    self._send_log_generated_notification(log_file_name, is_last=False)

            self.trace_thread = threading.Thread(target=generate_trace_logs, name="trace-log-generator")
            self.trace_thread.daemon = True
            self.trace_thread.start()

            # Return SUCCESS per O-RAN spec
            return {"status": "SUCCESS"}

        except Exception as e:
            logger.error(f"Error in start-trace-logs: {e}")
            return {"status": "FAILURE"}

    def _handle_stop_trace_logs(self, rpc_path, input_params, event, private_data):
        """
        Handle stop-trace-logs RPC.

        Per O-RAN spec 3.1.12.2 step 5:
        - Receive stop-trace-logs RPC
        - Return SUCCESS status
        - Generate final batch of trace logs
        - Send notification with is-notification-last=true (step 6)

        Args:
            rpc_path: XPath to the RPC
            input_params: Input parameters (none for this RPC)
            event: Sysrepo event type
            private_data: User private data

        Returns:
            Dict with status=SUCCESS
        """
        logger.info("Received stop-trace-logs RPC")

        try:
            self.trace_active = False

            # Generate final batch in background thread
            def generate_final_batch():
                time.sleep(2)  # Simulate log finalization delay

                log_file_name = f"o-ran/log/trace_final_{datetime.now().strftime('%Y%m%d_%H%M%S')}.gz"
                logger.info(f"Generated final trace log batch: {log_file_name}")

                # Send final notification with is-notification-last=true
                self._send_log_generated_notification(log_file_name, is_last=True)

            final_thread = threading.Thread(target=generate_final_batch, name="trace-final-generator")
            final_thread.daemon = True
            final_thread.start()

            return {"status": "SUCCESS"}

        except Exception as e:
            logger.error(f"Error in stop-trace-logs: {e}")
            return {"status": "FAILURE"}

    def _send_log_generated_notification(self, log_file_name: str, is_last: bool):
        """
        Send trace-log-generated notification.

        Per O-RAN spec 3.1.12.2:
        - Step 2: is-notification-last=false (more logs will follow)
        - Step 6: is-notification-last=true (final notification)

        Args:
            log_file_name: Path to generated log file
            is_last: True for final notification, False for intermediate
        """
        try:
            # log-file-name is a leaf-list in YANG, so it must be a Python list
            # is-notification-last is a leaf (boolean), so it's a single value
            notification_data = {
                "log-file-name": [log_file_name],
                "is-notification-last": is_last
            }

            xpath = "/o-ran-trace:trace-log-generated"
            logger.info(f"Sending trace-log-generated notification: {log_file_name}, is-last={is_last}")
            self.netconf.running.notification_send(xpath, notification_data)

        except Exception as e:
            logger.error(f"Failed to send trace-log-generated notification: {e}")
