"""
O-RAN User Management Feature Implementation.

Syncs users created via o-ran-usermgmt edit-config to the netopeer2
ietf-netconf-server SSH client-authentication, so NETCONF clients can
connect as the newly created users with password authentication.

Reference: O-RAN.WG4.TS.CONF.0-R004-v13.00 Section 3.1.8.x
"""

from util.logging import get_pynts_logger
from core.netconf import Netconf, Datastore

logger = get_pynts_logger("feature-o-ran-usermgmt")


class ORanUsermgmtFeature:
    """Sync o-ran-usermgmt users to netopeer2 SSH authentication."""

    def __init__(self) -> None:
        self.netconf = Netconf()

    def start(self) -> None:
        """Subscribe to o-ran-usermgmt module changes."""
        logger.info("Starting O-RAN user management feature")
        try:
            self.netconf.running.subscribe_module_change(
                "o-ran-usermgmt",
                None,
                self._handle_user_change
            )
            logger.info("Subscribed to o-ran-usermgmt module changes")
        except Exception as e:
            logger.error(f"Failed to start usermgmt feature: {e}")

    def _handle_user_change(self, event, request_id, changes, private_data):
        """Handle user creation/modification in o-ran-usermgmt."""
        # Only process after commit (DONE event)
        event_str = str(event)
        if "CHANGE" in event_str and "DONE" not in event_str:
            return

        logger.info(f"User management change event: {event}")

        try:
            # Read current users from o-ran-usermgmt
            data = self.netconf.running.get_data("/o-ran-usermgmt:users")
            if not data:
                return

            users_data = data.get("o-ran-usermgmt:users", data.get("users", {}))
            users = users_data.get("user", [])
            if isinstance(users, dict):
                users = [users]

            # Sync each user to netopeer2 SSH authentication
            for user in users:
                name = user.get("name", "")
                password = user.get("password", "")
                account_type = user.get("account-type", "")
                enabled = user.get("enabled", True)

                if not name or name == "netconf":
                    continue  # Skip default user

                if account_type == "PASSWORD" and password and enabled:
                    self._add_ssh_user(name, password)

        except Exception as e:
            logger.error(f"Error syncing users: {e}")

    def _add_ssh_user(self, username: str, password: str):
        """Add user to netopeer2 SSH client-authentication."""
        try:
            import sysrepo
            # Set password leaf — sysrepo auto-creates the list entry
            xpath = (
                "/ietf-netconf-server:netconf-server"
                "/listen/endpoints/endpoint[name='ssh-endpoint-10830']"
                "/ssh/ssh-server-parameters/client-authentication"
                f"/users/user[name='{username}']/password"
            )
            with sysrepo.SysrepoConnection() as conn:
                with conn.start_session('running') as sess:
                    sess.set_item(xpath, f"$0${password}")
                    sess.apply_changes()
            logger.info(f"Added SSH user '{username}' to netopeer2 authentication")
        except Exception as e:
            logger.error(f"Failed to add SSH user '{username}': {e}")
