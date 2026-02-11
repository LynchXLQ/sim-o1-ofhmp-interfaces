"""
O-RAN UDP Echo Feature Implementation

This module implements the UDP Echo functionality as defined in o-ran-udp-echo.yang.
When enabled, the O-RU listens on UDP port 7 (RFC 862) and echoes back received datagrams.
"""

from util.logging import get_pynts_logger
import threading
import socket
from core.netconf import Netconf, Datastore
from util.threading import stop_event, sa_sleep

logger = get_pynts_logger("feature-o-ran-udp-echo")

UDP_ECHO_PORT = 7


class ORanUdpEchoFeature:
    def __init__(self) -> None:
        self.netconf = Netconf()
        self.udp_echo_enabled = False
        self.dscp_config = "EF"  # Default from YANG
        self.echo_replies_transmitted = 0
        self.lock = threading.Lock()
        self.server_thread = None
        self.server_socket = None

    def start(self) -> None:
        """Start the UDP echo feature by subscribing to config changes."""
        logger.info("Starting O-RAN UDP echo feature")
        try:
            # Subscribe to module changes for udp-echo configuration
            self.netconf.running.subscribe_module_change(
                "o-ran-udp-echo",
                None,  # xpath filter (None = all changes)
                self._handle_config_change
            )
            logger.info("Successfully subscribed to o-ran-udp-echo module changes")

            # Read initial configuration
            self._read_initial_config()

        except Exception as e:
            logger.error(f"Failed to start UDP echo feature: {e}")

    def _read_initial_config(self):
        """Read and apply initial UDP echo configuration."""
        try:
            config_data = self.netconf.running.get_data("/o-ran-udp-echo:udp-echo")
            if config_data:
                # Handle both formats: 'udp-echo' and 'o-ran-udp-echo:udp-echo'
                udp_echo = config_data.get("udp-echo", config_data.get("o-ran-udp-echo:udp-echo", {}))
                enabled = udp_echo.get("enable-udp-echo", False)
                dscp = udp_echo.get("dscp-config", "EF")

                logger.info(f"Initial config: enabled={enabled}, dscp={dscp}")

                with self.lock:
                    self.dscp_config = dscp
                    if enabled and not self.udp_echo_enabled:
                        self._start_udp_server()
                        self.udp_echo_enabled = True
                        logger.info("UDP echo server enabled from initial config")

        except Exception as e:
            logger.error(f"Could not read initial config: {e}")

    def _handle_config_change(self, event, req_id, changes, private_data):
        """Handle configuration changes for UDP echo."""
        logger.info(f"UDP echo config change event: {event}")

        try:
            for change in changes:
                logger.debug(f"Change: {change}")
                change_str = str(change)

                if "enable-udp-echo" in change_str:
                    # Extract the new value
                    enabled = self._extract_value(change)

                    with self.lock:
                        if enabled and not self.udp_echo_enabled:
                            self._start_udp_server()
                            self.udp_echo_enabled = True
                            logger.info("UDP echo server enabled")
                        elif not enabled and self.udp_echo_enabled:
                            self._stop_udp_server()
                            self.udp_echo_enabled = False
                            logger.info("UDP echo server disabled")

                elif "dscp-config" in change_str:
                    value = self._extract_string_value(change)
                    with self.lock:
                        self.dscp_config = value if value else "EF"
                        logger.info(f"DSCP config changed to: {self.dscp_config}")

        except Exception as e:
            logger.error(f"Error handling config change: {e}")

    def _extract_value(self, change):
        """Extract boolean value from change event."""
        try:
            if hasattr(change, 'new_value'):
                return bool(change.new_value)
            elif hasattr(change, 'value'):
                return bool(change.value)
            else:
                # Try to parse from string representation
                change_str = str(change).lower()
                return 'true' in change_str
        except Exception:
            return False

    def _extract_string_value(self, change):
        """Extract string value from change event."""
        try:
            if hasattr(change, 'new_value'):
                return str(change.new_value)
            elif hasattr(change, 'value'):
                return str(change.value)
            else:
                return None
        except Exception:
            return None

    def _start_udp_server(self):
        """Start the UDP echo server on port 7."""
        try:
            # Create UDP socket
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.settimeout(1.0)  # Allow periodic checking for stop
            self.server_socket.bind(('0.0.0.0', UDP_ECHO_PORT))

            # Start server thread
            self.server_thread = threading.Thread(target=self._udp_server_loop)
            self.server_thread.daemon = True
            self.server_thread.start()

            logger.info(f"UDP echo server started on port {UDP_ECHO_PORT}")

        except PermissionError:
            logger.error(f"Permission denied to bind to port {UDP_ECHO_PORT}. Root privileges required.")
        except Exception as e:
            logger.error(f"Failed to start UDP server: {e}")

    def _stop_udp_server(self):
        """Stop the UDP echo server."""
        try:
            if self.server_socket:
                self.server_socket.close()
                self.server_socket = None
            logger.info("UDP echo server stopped")
        except Exception as e:
            logger.error(f"Error stopping UDP server: {e}")

    def _udp_server_loop(self):
        """Main loop for UDP echo server."""
        logger.info("UDP echo server loop started")

        while not stop_event.is_set():
            with self.lock:
                if not self.udp_echo_enabled:
                    break

            try:
                if self.server_socket is None:
                    break

                # Receive UDP datagram
                data, client_addr = self.server_socket.recvfrom(65535)

                logger.debug(f"Received {len(data)} bytes from {client_addr}")

                # Echo back the datagram
                with self.lock:
                    dscp = self.dscp_config

                # Set DSCP if REFLECTIVE mode (copy from received packet)
                # For EF mode, use default socket behavior
                # Note: Setting DSCP requires IP_TOS socket option

                self.server_socket.sendto(data, client_addr)

                # Increment counter
                with self.lock:
                    self.echo_replies_transmitted += 1
                    count = self.echo_replies_transmitted

                logger.debug(f"Echoed {len(data)} bytes to {client_addr}, total replies: {count}")

                # Update operational data
                self._update_operational_counter()

            except socket.timeout:
                # Normal timeout, just continue
                continue
            except Exception as e:
                if self.udp_echo_enabled:  # Only log if we're supposed to be running
                    logger.error(f"Error in UDP server loop: {e}")
                break

        logger.info("UDP echo server loop finished")

    def _update_operational_counter(self):
        """Update the echo-replies-transmitted counter in operational datastore."""
        try:
            with self.lock:
                count = self.echo_replies_transmitted

            operational_data = {
                "udp-echo": {
                    "echo-replies-transmitted": count
                }
            }

            self.netconf.set_data(Datastore.OPERATIONAL, "o-ran-udp-echo", operational_data)

        except Exception as e:
            logger.debug(f"Could not update operational counter: {e}")
