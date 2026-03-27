

from util.logging import get_pynts_logger
from core.netconf import Netconf, Datastore

logger = get_pynts_logger("feature-o-ran-software-management")

class ORanSoftwareManagementFeature:
    """
    O-RAN Software Management feature implementation.

    Implements the o-ran-software-management YANG module RPCs:
    - software-activate: Activate a previously installed software slot
    - software-download: Download software to the O-RU (optional)
    - software-install: Install downloaded software (optional)
    """

    def __init__(self) -> None:
        self.netconf = Netconf()
        self.last_download_path = ""  # Track last downloaded file for install error simulation

    def start(self) -> None:
        """Start the O-RAN software management feature by subscribing to RPCs."""
        logger.info("Starting O-RAN software management feature")
        try:
            # Subscribe to software-activate RPC
            self.netconf.running.subscribe_rpc_call(
                "/o-ran-software-management:software-activate",
                self._handle_software_activate_rpc
            )
            logger.info("Successfully subscribed to software-activate RPC")

            # Subscribe to software-download RPC
            self.netconf.running.subscribe_rpc_call(
                "/o-ran-software-management:software-download",
                self._handle_software_download_rpc
            )
            logger.info("Successfully subscribed to software-download RPC")

            # Subscribe to software-install RPC
            self.netconf.running.subscribe_rpc_call(
                "/o-ran-software-management:software-install",
                self._handle_software_install_rpc
            )
            logger.info("Successfully subscribed to software-install RPC")

            # Restore persisted activation state from previous run (supports 3.1.7.2 reset test)
            self._restore_activation_state()

        except Exception as e:
            logger.error(f"Failed to start O-RAN software management feature: {e}")

    def _restore_activation_state(self):
        """Restore software slot activation state persisted before reset."""
        import json
        state_file = "/data/.sw-activation-state.json"
        try:
            with open(state_file, "r") as f:
                state = json.load(f)
            activated_slot = state.get("activated_slot")
            if activated_slot:
                logger.info(f"Restoring activation state: {activated_slot} was activated before reset")
                all_slots = ['SLOT0', 'SLOT1']
                for slot in all_slots:
                    active_xpath = f"/o-ran-software-management:software-inventory/software-slot[name='{slot}']/active"
                    running_xpath = f"/o-ran-software-management:software-inventory/software-slot[name='{slot}']/running"
                    if slot == activated_slot:
                        self.netconf.set_data(Datastore.OPERATIONAL, active_xpath, "true")
                        self.netconf.set_data(Datastore.OPERATIONAL, running_xpath, "true")
                        logger.info(f"Set slot {slot} active=true, running=true (post-reset)")
                    else:
                        self.netconf.set_data(Datastore.OPERATIONAL, active_xpath, "false")
                        self.netconf.set_data(Datastore.OPERATIONAL, running_xpath, "false")
                        logger.debug(f"Set slot {slot} active=false, running=false")
                # Clean up state file
                import os
                os.remove(state_file)
                logger.info("Cleaned up activation state file")
        except FileNotFoundError:
            logger.debug("No persisted activation state found (normal for fresh start)")
        except Exception as e:
            logger.debug(f"Could not restore activation state: {e}")

    def _handle_software_activate_rpc(self, rpc_path, input_params, event, private_data):
        """
        Handle incoming software-activate RPC calls.

        According to O-RAN.WG4.MP.0-v13.00 specification section 3.1.7.1:
        "Activate a previously installed software. The software slot is identified
        by its name parameter provided as an input. Upon successful activation,
        the parameter 'active' for the activated slot is set to 'True' and for
        all other slots it is set to 'False'. The parameter 'running' remains
        unchanged until a subsequent system restart."

        Args:
            rpc_path: XPath to the RPC (/o-ran-software-management:software-activate)
            input_params: Dictionary containing 'slot-name' (mandatory)
            event: Sysrepo event type
            private_data: User private data

        Returns:
            Dictionary with 'status' (STARTED|FAILED), optional 'error-message',
            and optional 'notification-timeout'
        """
        logger.info(f"Received software-activate RPC with params: {input_params}")

        try:
            # Extract slot-name from input parameters
            if not input_params or 'slot-name' not in input_params:
                error_msg = "Missing required parameter 'slot-name'"
                logger.error(f"Software activation failed: {error_msg}")
                return {
                    'status': 'FAILED',
                    'error-message': error_msg
                }

            slot_name = input_params['slot-name']
            logger.info(f"Activating software slot: {slot_name}")

            # Update slot activation states directly using XPath
            # Set target slot active=true, all others active=false
            # Note: 'running' flag is NOT changed (requires system reset)

            # Known slot names (SLOT0 and SLOT1 from the inventory)
            all_slots = ['SLOT0', 'SLOT1']

            # Validate that requested slot is in the list
            if slot_name not in all_slots:
                error_msg = f"Slot '{slot_name}' not found in inventory (valid slots: {', '.join(all_slots)})"
                logger.error(error_msg)
                return {
                    'status': 'FAILED',
                    'error-message': error_msg
                }

            logger.info(f"Updating slot activation states - setting {slot_name} as active")

            try:
                for slot in all_slots:
                    slot_xpath = f"/o-ran-software-management:software-inventory/software-slot[name='{slot}']/active"
                    if slot == slot_name:
                        self.netconf.set_data(Datastore.OPERATIONAL, slot_xpath, "true")
                        logger.info(f"Set slot {slot} active=true")
                    else:
                        self.netconf.set_data(Datastore.OPERATIONAL, slot_xpath, "false")
                        logger.debug(f"Set slot {slot} active=false")

                # Persist activated slot for survival across restarts (3.1.7.2)
                try:
                    import json
                    state_file = "/data/.sw-activation-state.json"
                    with open(state_file, "w") as f:
                        json.dump({"activated_slot": slot_name}, f)
                    logger.info(f"Persisted activation state to {state_file}")
                except Exception as pe:
                    logger.debug(f"Could not persist activation state: {pe}")

            except Exception as e:
                error_msg = f"Failed to update slot states: {str(e)}"
                logger.error(error_msg, exc_info=True)
                return {
                    'status': 'FAILED',
                    'error-message': error_msg
                }

            logger.info(f"Software slot '{slot_name}' activated successfully")

            # Send activation-event notification
            import threading
            def send_activation_notification():
                import time
                time.sleep(1)  # Brief delay to simulate activation
                try:
                    notification_data = {
                        "slot-name": slot_name,
                        "status": "COMPLETED"
                    }
                    self.netconf.running.notification_send(
                        "/o-ran-software-management:activation-event",
                        notification_data
                    )
                    logger.info(f"Sent activation-event notification: slot={slot_name}, COMPLETED")
                except Exception as e:
                    logger.error(f"Failed to send activation notification: {e}")

            activation_thread = threading.Thread(target=send_activation_notification, daemon=True)
            activation_thread.start()

            # Return success response
            return {
                'status': 'STARTED',
                'notification-timeout': 30
            }

        except Exception as e:
            error_msg = f"Unexpected error during software activation: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return {
                'status': 'FAILED',
                'error-message': error_msg
            }

    def _handle_software_download_rpc(self, rpc_path, input_params, event, private_data):
        """Handle software-download RPC per O-RAN spec 3.1.6.1."""
        logger.info(f"Received software-download RPC: {input_params}")

        try:
            # Extract remote-file-path from input
            remote_file_path = None
            if input_params:
                remote_file_path = input_params.get("remote-file-path",
                                  input_params.get("o-ran-software-management:remote-file-path"))

            if not remote_file_path:
                return {"status": "FAILED", "error-message": "Missing remote-file-path"}

            logger.info(f"Software download requested from: {remote_file_path}")
            self.last_download_path = remote_file_path

            # Simulate download in background and send notification
            import threading
            def simulate_download():
                import time
                time.sleep(2)  # Simulate download delay

                # Determine status: simulate error for invalid file paths
                path_lower = remote_file_path.lower()
                if "invalid" in path_lower or "nonexist" in path_lower:
                    status = "FILE_NOT_FOUND"
                elif "auth" in path_lower or "denied" in path_lower:
                    status = "AUTHENTICATION_ERROR"
                elif "timeout" in path_lower:
                    status = "TIMEOUT"
                else:
                    status = "COMPLETED"

                try:
                    notification_data = {
                        "file-name": remote_file_path,
                        "status": status
                    }
                    self.netconf.running.notification_send(
                        "/o-ran-software-management:download-event",
                        notification_data
                    )
                    logger.info(f"Sent download-event notification: {status}")
                except Exception as e:
                    logger.error(f"Failed to send download notification: {e}")

            download_thread = threading.Thread(target=simulate_download, daemon=True)
            download_thread.start()

            return {"status": "STARTED", "notification-timeout": 30}

        except Exception as e:
            logger.error(f"Error handling software-download: {e}")
            return {"status": "FAILED", "error-message": str(e)}

    def _handle_software_install_rpc(self, rpc_path, input_params, event, private_data):
        """Handle software-install RPC per O-RAN spec 3.1.6.1."""
        logger.info(f"Received software-install RPC: {input_params}")

        try:
            slot_name = None
            if input_params:
                slot_name = input_params.get("slot-name",
                           input_params.get("o-ran-software-management:slot-name"))

            if not slot_name:
                return {"status": "FAILED", "error-message": "Missing slot-name"}

            logger.info(f"Software install requested to slot: {slot_name}")

            # Get file-names for error simulation
            file_names = ""
            if input_params:
                file_names = str(input_params.get("file-names",
                                input_params.get("o-ran-software-management:file-names", "")))

            # Simulate install in background and send notification
            import threading
            def simulate_install():
                import time
                time.sleep(2)  # Simulate install delay

                # Determine status: simulate error if last download was corrupt/invalid
                fn_lower = file_names.lower()
                dl_lower = self.last_download_path.lower()
                if "corrupt" in fn_lower or "corrupt" in dl_lower or "bad" in fn_lower:
                    status = "FILE_ERROR"
                elif "integrity" in fn_lower or "integrity" in dl_lower:
                    status = "INTEGRITY_ERROR"
                else:
                    status = "COMPLETED"
                    # Only update slot status for successful installs
                    try:
                        slot_xpath = f"/o-ran-software-management:software-inventory/software-slot[name='{slot_name}']/status"
                        self.netconf.set_data(Datastore.OPERATIONAL, slot_xpath, "VALID")
                    except Exception as e:
                        logger.debug(f"Could not update slot status: {e}")

                try:
                    notification_data = {
                        "slot-name": slot_name,
                        "status": status
                    }
                    self.netconf.running.notification_send(
                        "/o-ran-software-management:install-event",
                        notification_data
                    )
                    logger.info(f"Sent install-event notification: {status}")
                except Exception as e:
                    logger.error(f"Failed to send install notification: {e}")

            install_thread = threading.Thread(target=simulate_install, daemon=True)
            install_thread.start()

            return {"status": "STARTED", "notification-timeout": 30}

        except Exception as e:
            logger.error(f"Error handling software-install: {e}")
            return {"status": "FAILED", "error-message": str(e)}
