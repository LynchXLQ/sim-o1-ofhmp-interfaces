# /*************************************************************************
# *
# * Copyright 2025 highstreet technologies and others
# *
# * Licensed under the Apache License, Version 2.0 (the "License");
# * you may not use this file except in compliance with the License.
# * You may obtain a copy of the License at
# *
# *     http://www.apache.org/licenses/LICENSE-2.0
# *
# * Unless required by applicable law or agreed to in writing, software
# * distributed under the License is distributed on an "AS IS" BASIS,
# * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# * See the License for the specific language governing permissions and
# * limitations under the License.
# ***************************************************************************/

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
        except Exception as e:
            logger.error(f"Failed to start O-RAN software management feature: {e}")

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
                        # Activate target slot
                        self.netconf.set_data(Datastore.OPERATIONAL, slot_xpath, "true")
                        logger.info(f"Set slot {slot} active=true")
                    else:
                        # Deactivate all other slots
                        self.netconf.set_data(Datastore.OPERATIONAL, slot_xpath, "false")
                        logger.debug(f"Set slot {slot} active=false")

            except Exception as e:
                error_msg = f"Failed to update slot states: {str(e)}"
                logger.error(error_msg, exc_info=True)
                return {
                    'status': 'FAILED',
                    'error-message': error_msg
                }

            logger.info(f"Software slot '{slot_name}' activated successfully")

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
