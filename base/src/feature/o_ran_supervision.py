

from util.logging import get_pynts_logger
import threading
from datetime import datetime, timedelta, timezone
from core.netconf import Netconf, Datastore
from util.threading import stop_event, sa_sleep

logger = get_pynts_logger("feature-o-ran-supervision")

class ORanSupervisionFeature:
    def __init__(self) -> None:
        self.netconf = Netconf()
        self.supervision_active = False
        self.supervision_interval = 60  # Default 60 seconds
        self.guard_timer_overhead = 10  # Default 10 seconds
        self.last_watchdog_reset = None
        self.lock = threading.Lock()
        self.notification_thread = None
        self.timeout_monitor_thread = None

    def start(self) -> None:
        """Start the supervision feature by subscribing to RPC."""
        logger.info("Starting O-RAN supervision feature")
        try:
            # Initialize operational state
            self._initialize_operational_state()

            # Subscribe to supervision-watchdog-reset RPC
            self.netconf.running.subscribe_rpc_call(
                "/o-ran-supervision:supervision-watchdog-reset",
                self._handle_watchdog_reset_rpc
            )
            logger.info("Successfully subscribed to supervision-watchdog-reset RPC")

            # Start timeout monitoring thread
            self.timeout_monitor_thread = threading.Thread(target=self._timeout_monitor)
            self.timeout_monitor_thread.daemon = True
            self.timeout_monitor_thread.start()
            logger.info("Timeout monitoring thread started")

        except Exception as e:
            logger.error(f"Failed to start supervision feature: {e}")

    def _initialize_operational_state(self):
        """Initialize supervision operational state."""
        try:
            # Initialize operational state with default values from YANG
            operational_data = {
                "supervision": {
                    "cu-plane-monitoring": {
                        "configured-cu-monitoring-interval": 160  # Default from YANG schema
                    }
                }
            }

            # Set operational state
            self.netconf.set_data(Datastore.OPERATIONAL, "o-ran-supervision", operational_data)
            logger.info("Initialized supervision operational state")

        except Exception as e:
            logger.error(f"Failed to initialize operational state: {e}")

    def _handle_watchdog_reset_rpc(self, rpc_path, input_params, event, private_data):
        """Handle incoming supervision-watchdog-reset RPC calls."""
        logger.info(f"Received supervision-watchdog-reset RPC")
        logger.debug(f"Input params: {input_params}")

        try:
            # Extract parameters from input
            # If not provided, use values from running configuration
            supervision_interval = None
            guard_timer_overhead = None

            if input_params:
                # Parse input_params dictionary
                if "supervision-notification-interval" in input_params:
                    supervision_interval = int(input_params["supervision-notification-interval"])
                if "guard-timer-overhead" in input_params:
                    guard_timer_overhead = int(input_params["guard-timer-overhead"])

            # If parameters not provided in RPC, read from running configuration
            if supervision_interval is None or guard_timer_overhead is None:
                try:
                    config_data = self.netconf.running.get_data("/o-ran-supervision:supervision/cu-plane-monitoring")
                    if config_data:
                        cu_monitoring = config_data.get("o-ran-supervision:supervision", {}).get("cu-plane-monitoring", {})
                        if supervision_interval is None:
                            configured_interval = cu_monitoring.get("configured-cu-monitoring-interval")
                            if configured_interval is not None:
                                supervision_interval = int(configured_interval)
                                logger.debug(f"Using configured supervision interval: {supervision_interval}s")
                        # Note: guard-timer-overhead is not configurable, only comes from RPC
                except Exception as e:
                    logger.debug(f"Could not read configured values: {e}")

            # Apply defaults if still not set
            if supervision_interval is None:
                supervision_interval = 60  # Default from YANG
                logger.debug("Using default supervision interval: 60s")
            if guard_timer_overhead is None:
                guard_timer_overhead = 10  # Default from YANG
                logger.debug("Using default guard timer overhead: 10s")

            with self.lock:
                self.supervision_interval = supervision_interval
                self.guard_timer_overhead = guard_timer_overhead
                self.supervision_active = True
                self.last_watchdog_reset = datetime.now(timezone.utc)

                # Calculate next-update-at (current time + interval)
                next_update = self.last_watchdog_reset + timedelta(seconds=supervision_interval)
                next_update_str = next_update.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

                logger.info(f"Supervision activated: interval={supervision_interval}s, guard={guard_timer_overhead}s")
                logger.info(f"Next update at: {next_update_str}")

                # Start or restart the notification thread
                if self.notification_thread is None or not self.notification_thread.is_alive():
                    self.notification_thread = threading.Thread(target=self._notification_sender)
                    self.notification_thread.daemon = True
                    self.notification_thread.start()
                    logger.debug("Notification sender thread started")

            # Update operational state in background to avoid blocking RPC response
            def update_operational_state_async():
                try:
                    operational_data = {
                        "supervision": {
                            "cu-plane-monitoring": {
                                "configured-cu-monitoring-interval": supervision_interval
                            }
                        }
                    }
                    self.netconf.set_data(Datastore.OPERATIONAL, "o-ran-supervision", operational_data)
                    logger.debug("Updated operational state with new interval")
                except Exception as e:
                    logger.error(f"Failed to update operational state: {e}")

            # Start async update in background thread
            update_thread = threading.Thread(target=update_operational_state_async)
            update_thread.daemon = True
            update_thread.start()

            # Return RPC output immediately without waiting for operational state update
            # Sysrepo handles namespacing automatically, so use plain leaf names
            output = {
                "next-update-at": next_update_str
            }

            logger.debug(f"Returning RPC output: {output}")
            return output

        except Exception as e:
            logger.error(f"Error handling supervision-watchdog-reset RPC: {e}")
            # Return error message in output
            return {"error-message": str(e)}

    def _notification_sender(self):
        """Timer thread that sends supervision notifications periodically."""
        logger.info("Supervision notification sender thread started")

        while not stop_event.is_set():
            with self.lock:
                if not self.supervision_active:
                    logger.debug("Supervision not active, notification sender waiting")
                    sa_sleep(1)
                    continue

                interval = self.supervision_interval

            # Wait for the supervision interval
            logger.debug(f"Waiting {interval} seconds before sending supervision notification")
            sa_sleep(interval)

            # Check if still active
            with self.lock:
                if not self.supervision_active:
                    logger.debug("Supervision deactivated, skipping notification")
                    continue

            # Get NETCONF session-id
            try:
                session_id = self._get_netconf_session_id()

                # Build notification data according to YANG schema
                notification_data = {
                    "session-id": session_id
                }

                logger.info(f"Sending supervision-notification with session-id={session_id}")

                # Send notification using sysrepo
                self.netconf.running.notification_send(
                    "/o-ran-supervision:supervision-notification",
                    notification_data
                )
                logger.debug("Supervision notification sent successfully")

            except Exception as e:
                logger.error(f"Failed to send supervision notification: {e}")

        logger.info("Supervision notification sender thread finished")

    def _timeout_monitor(self):
        """Monitor thread that detects supervision timeout and raises alarms."""
        logger.info("Supervision timeout monitor thread started")
        timeout_alarm_active = False

        while not stop_event.is_set():
            sa_sleep(5)  # Check every 5 seconds

            with self.lock:
                if not self.supervision_active or self.last_watchdog_reset is None:
                    # Don't clear alarm here - the alarm should persist after timeout
                    # until supervision is explicitly resumed via watchdog reset
                    # If we cleared here, we'd clear it right after raising due to timeout
                    continue

                current_time = datetime.now(timezone.utc)
                timeout_threshold = self.last_watchdog_reset + timedelta(
                    seconds=self.supervision_interval + self.guard_timer_overhead
                )

                # Check if timeout has occurred
                if current_time > timeout_threshold:
                    if not timeout_alarm_active:
                        logger.warning(f"Supervision timeout detected! No watchdog reset received within {self.supervision_interval + self.guard_timer_overhead}s")
                        self._raise_supervision_timeout_alarm()
                        timeout_alarm_active = True
                        # Deactivate supervision
                        self.supervision_active = False
                else:
                    if timeout_alarm_active:
                        logger.info("Supervision timeout cleared - watchdog reset received")
                        self._clear_supervision_timeout_alarm()
                        timeout_alarm_active = False

        logger.info("Supervision timeout monitor thread finished")

    def _get_netconf_session_id(self):
        """Retrieve the current NETCONF session ID from netconf-state."""
        try:
            # Query the NETCONF monitoring state for current sessions
            # The path format needs to match ietf-netconf-monitoring YANG module
            sessions_data = self.netconf.operational.get_data(
                "/ietf-netconf-monitoring:netconf-state/sessions"
            )

            # Extract session-id from the sessions data
            if sessions_data and "ietf-netconf-monitoring:sessions" in sessions_data:
                sessions = sessions_data["ietf-netconf-monitoring:sessions"]
                if "session" in sessions and len(sessions["session"]) > 0:
                    # Get the first session (or find the correct one)
                    for session in sessions["session"]:
                        # Use the session that is currently active
                        session_id = session.get("session-id")
                        if session_id:
                            logger.debug(f"Retrieved NETCONF session-id: {session_id}")
                            return session_id

            # Fallback to default session-id if not found
            logger.warning("Could not retrieve NETCONF session-id from monitoring, using default value 1")
            return 1

        except Exception as e:
            logger.error(f"Error retrieving NETCONF session-id: {e}")
            # Return default session-id on error
            return 1

    def _raise_supervision_timeout_alarm(self):
        """Raise supervision-watchdog-timeout alarm via o-ran-fm."""
        try:
            # Build alarm notification according to o-ran-fm YANG schema
            alarm_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

            # Use fault-id 3 for supervision timeout (uint16 as per YANG schema)
            # fault-id must be an integer, not a string
            alarm_data = {
                "fault-id": 3,
                "fault-source": "o-ran-supervision",
                "affected-objects": [
                    {
                        "name": "supervision-watchdog"
                    }
                ],
                "fault-severity": "MAJOR",
                "is-cleared": False,
                "fault-text": "Supervision watchdog timeout - no watchdog reset received",
                "event-time": alarm_time
            }

            logger.info("Raising supervision-watchdog-timeout alarm (fault-id=3)")

            # Add alarm to active-alarm-list in operational datastore
            try:
                # Get current alarms
                current_alarms = []
                try:
                    alarm_list_data = self.netconf.operational.get_data("/o-ran-fm:active-alarm-list")
                    if alarm_list_data:
                        if "o-ran-fm:active-alarm-list" in alarm_list_data:
                            current_alarms = alarm_list_data["o-ran-fm:active-alarm-list"].get("active-alarms", [])
                        elif "active-alarm-list" in alarm_list_data:
                            current_alarms = alarm_list_data["active-alarm-list"].get("active-alarms", [])
                except Exception:
                    pass

                # Remove any existing supervision alarms (fault-id 3) before adding new one
                current_alarms = [alarm for alarm in current_alarms if str(alarm.get("fault-id")) != "3"]
                
                # Add new alarm
                current_alarms.append(alarm_data)

                # Update operational datastore
                updated_data = {
                    "active-alarm-list": {
                        "active-alarms": current_alarms
                    }
                }
                self.netconf.set_data(Datastore.OPERATIONAL, "o-ran-fm", updated_data)
                logger.debug("Added supervision timeout alarm to active-alarm-list")
            except Exception as e:
                logger.error(f"Failed to add alarm to active-alarm-list: {e}")

            # Send alarm notification
            self.netconf.running.notification_send(
                "/o-ran-fm:alarm-notif",
                alarm_data
            )

        except Exception as e:
            logger.error(f"Failed to raise supervision timeout alarm: {e}")

    def _clear_supervision_timeout_alarm(self):
        """Clear supervision-watchdog-timeout alarm via o-ran-fm."""
        import sysrepo
        try:
            alarm_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            logger.info("Clearing supervision-watchdog-timeout alarm")

            # Since active-alarms is a keyless list, we can't delete individual items
            # Instead: delete entire list, then recreate with filtered items
            try:
                # First read current alarms
                alarm_list_data = self.netconf.operational.get_data("/o-ran-fm:active-alarm-list")
                active_alarms = []
                if alarm_list_data:
                    if "o-ran-fm:active-alarm-list" in alarm_list_data:
                        active_alarms = alarm_list_data["o-ran-fm:active-alarm-list"].get("active-alarms", [])
                    elif "active-alarm-list" in alarm_list_data:
                        active_alarms = alarm_list_data["active-alarm-list"].get("active-alarms", [])

                logger.debug(f"Active alarms before filter: {len(active_alarms)} alarms")

                # Filter out supervision alarms (fault-id 3)
                filtered_alarms = [alarm for alarm in active_alarms
                                   if str(alarm.get("fault-id")) != "3"]

                logger.debug(f"Active alarms after filter: {len(filtered_alarms)} alarms")

                # Delete entire active-alarm-list then recreate with filtered items
                with sysrepo.SysrepoConnection() as conn:
                    with conn.start_session('operational') as sess:
                        try:
                            # Delete the container
                            sess.delete_oper_item("/o-ran-fm:active-alarm-list")
                            sess.apply_changes()
                            logger.info("Deleted active-alarm-list container")
                        except Exception as e:
                            logger.debug(f"Could not delete container: {e}")

                # Recreate with filtered alarms using merge
                if filtered_alarms:
                    updated_data = {
                        "active-alarm-list": {
                            "active-alarms": filtered_alarms
                        }
                    }
                    self.netconf.set_data(Datastore.OPERATIONAL, "o-ran-fm", updated_data)
                    logger.info("Recreated active-alarm-list without supervision alarm")
                else:
                    logger.info("No alarms remaining after filter")

            except Exception as e:
                logger.error(f"Could not delete alarm from list: {e}")

            # Send alarm cleared notification
            alarm_data = {
                "fault-id": 3,
                "fault-source": "o-ran-supervision",
                "affected-objects": [{"name": "supervision-watchdog"}],
                "fault-severity": "MAJOR",
                "is-cleared": True,
                "fault-text": "Supervision watchdog timeout cleared",
                "event-time": alarm_time
            }

            self.netconf.running.notification_send("/o-ran-fm:alarm-notif", alarm_data)
            logger.debug("Sent alarm cleared notification")

        except Exception as e:
            logger.error(f"Failed to clear supervision timeout alarm: {e}")
