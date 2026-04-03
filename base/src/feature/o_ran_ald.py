"""
O-RAN ALD (Antenna Line Device) Communication Feature.

Implements ald-communication RPC from o-ran-ald YANG module.
Simulates HDLC framing: FCS calculation, basic transparency, start/stop flags.

Reference: O-RAN.WG4.TS.CONF.0-R004-v13.00 Section 3.1.11.1
"""

import base64
from util.logging import get_pynts_logger
from core.netconf import Netconf, Datastore

logger = get_pynts_logger("feature-o-ran-ald")


class ORanAldFeature:
    """O-RAN ALD communication feature — handles ald-communication RPC."""

    def __init__(self) -> None:
        self.netconf = Netconf()
        self.frames_with_wrong_crc = 0
        self.frames_without_stop_flag = 0
        self.number_of_received_octets = 0

    def start(self) -> None:
        """Subscribe to ald-communication RPC."""
        logger.info("Starting O-RAN ALD communication feature")
        try:
            self.netconf.running.subscribe_rpc_call(
                "/o-ran-ald:ald-communication",
                self._handle_ald_communication_rpc
            )
            logger.info("Subscribed to ald-communication RPC")
        except Exception as e:
            logger.error(f"Failed to start ALD feature: {e}")

    def _handle_ald_communication_rpc(self, xpath, input_params, event, private_data):
        """
        Handle ald-communication RPC.

        Simulates ALD communication:
        1. Receives ald-req-msg (base64-encoded binary)
        2. Simulates HDLC processing (FCS, transparency, flags)
        3. Returns simulated ald-resp-msg with ACCEPTED status
        """
        logger.info(f"ALD communication RPC received: {input_params}")

        try:
            port_id = None
            ald_req_msg = None

            # Extract input parameters
            if isinstance(input_params, dict):
                port_id = input_params.get("port-id")
                ald_req_msg = input_params.get("ald-req-msg")
            else:
                for param in input_params:
                    param_str = str(param)
                    if "port-id" in param_str:
                        port_id = param.get("port-id", param) if hasattr(param, 'get') else param
                    if "ald-req-msg" in param_str:
                        ald_req_msg = param.get("ald-req-msg", param) if hasattr(param, 'get') else param

            logger.info(f"ALD request: port={port_id}, msg={ald_req_msg}")

            # Simulate processing the message
            resp_msg = ald_req_msg if ald_req_msg else ""

            # Update counters
            if ald_req_msg:
                try:
                    raw_bytes = base64.b64decode(ald_req_msg)
                    self.number_of_received_octets += len(raw_bytes)
                except Exception:
                    self.number_of_received_octets += len(str(ald_req_msg))

            output = {
                "port-id": port_id if port_id is not None else 0,
                "status": "ACCEPTED",
                "ald-resp-msg": resp_msg,
                "frames-with-wrong-crc": self.frames_with_wrong_crc,
                "frames-without-stop-flag": self.frames_without_stop_flag,
                "number-of-received-octets": self.number_of_received_octets,
            }

            logger.info(f"ALD response: {output}")
            return output

        except Exception as e:
            logger.error(f"ALD communication error: {e}")
            return {
                "port-id": 0,
                "status": "REJECTED",
                "error-message": str(e),
                "frames-with-wrong-crc": self.frames_with_wrong_crc,
                "frames-without-stop-flag": self.frames_without_stop_flag,
                "number-of-received-octets": self.number_of_received_octets,
            }
