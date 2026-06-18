

import os
import socket
import tarfile
import threading
import time
import zipfile
from urllib.parse import urlparse

import paramiko

from util.logging import get_pynts_logger
from core.netconf import Netconf, Datastore


# Algorithm set forbidden by O-RAN SPS 4.1 (SHA-1 host keys / KEX / MAC and
# weak/legacy ciphers). paramiko's defaults still include them; without this
# filter the outbound SSH client advertises them in its KEXINIT, which a
# wire audit flags as SPS 4.1 non-compliance.
_SPS_4_1_DISABLED_ALGORITHMS = {
    "keys":    ["ssh-rsa", "ssh-rsa-cert-v01@openssh.com",
                "ssh-dss", "ssh-dss-cert-v01@openssh.com"],
    "kex":     ["diffie-hellman-group1-sha1",
                "diffie-hellman-group14-sha1",
                "diffie-hellman-group-exchange-sha1"],
    "ciphers": ["3des-cbc",
                "aes128-cbc", "aes192-cbc", "aes256-cbc"],
    "macs":    ["hmac-sha1",  "hmac-md5",
                "hmac-sha1-96", "hmac-md5-96"],
}

logger = get_pynts_logger("feature-o-ran-software-management")


class ORanSoftwareManagementFeature:
    """
    O-RAN Software Management feature implementation.

    Implements the o-ran-software-management YANG module RPCs per WG4 spec 3.1.6 / 3.1.7:
    - software-activate: Activate a previously installed software slot
    - software-download: Real sFTP GET from the remote URL, map errors to O-RAN
                        status codes (AUTHENTICATION_ERROR, FILE_NOT_FOUND,
                        PROTOCOL_ERROR, TIMEOUT, APPLICATION_ERROR)
    - software-install: Real zip + manifest validation of the previously downloaded
                        package; FILE_ERROR for bad zip, INTEGRITY_ERROR for missing
                        manifest.xml, COMPLETED otherwise (slot status -> VALID)
    """

    # Local cache for downloaded software packages (per slot).
    DOWNLOAD_CACHE = "/var/cache/o-ran-sw"

    def __init__(self) -> None:
        self.netconf = Netconf()
        self.last_download_path = ""  # Path on the remote server
        self.last_local_file = ""     # Path of the cached copy on the O-RU
        os.makedirs(self.DOWNLOAD_CACHE, exist_ok=True)

    def start(self) -> None:
        logger.info("Starting O-RAN software management feature")
        try:
            self.netconf.running.subscribe_rpc_call(
                "/o-ran-software-management:software-activate",
                self._handle_software_activate_rpc,
            )
            self.netconf.running.subscribe_rpc_call(
                "/o-ran-software-management:software-download",
                self._handle_software_download_rpc,
            )
            self.netconf.running.subscribe_rpc_call(
                "/o-ran-software-management:software-install",
                self._handle_software_install_rpc,
            )
            logger.info("Successfully subscribed to software management RPCs")
            self._restore_activation_state()
        except Exception as e:
            logger.error(f"Failed to start O-RAN software management feature: {e}")

    # -------------------------------------------------------------------------
    # software-activate (unchanged semantics — no network I/O involved)
    # -------------------------------------------------------------------------

    def _restore_activation_state(self):
        import json
        state_file = "/data/.sw-activation-state.json"
        try:
            with open(state_file, "r") as f:
                state = json.load(f)
            activated_slot = state.get("activated_slot")
            if activated_slot:
                logger.info(f"Restoring activation state: {activated_slot} was activated before reset")
                for slot in ("SLOT0", "SLOT1"):
                    active_xpath = f"/o-ran-software-management:software-inventory/software-slot[name='{slot}']/active"
                    running_xpath = f"/o-ran-software-management:software-inventory/software-slot[name='{slot}']/running"
                    value = "true" if slot == activated_slot else "false"
                    self.netconf.set_data(Datastore.OPERATIONAL, active_xpath, value)
                    self.netconf.set_data(Datastore.OPERATIONAL, running_xpath, value)
                os.remove(state_file)
                logger.info("Cleaned up activation state file")
        except FileNotFoundError:
            logger.debug("No persisted activation state (normal for fresh start)")
        except Exception as e:
            logger.debug(f"Could not restore activation state: {e}")

    def _handle_software_activate_rpc(self, rpc_path, input_params, event, private_data):
        logger.info(f"Received software-activate RPC: {input_params}")
        if not input_params or "slot-name" not in input_params:
            return {"status": "FAILED", "error-message": "Missing required parameter 'slot-name'"}

        slot_name = input_params["slot-name"]
        all_slots = ("SLOT0", "SLOT1")
        if slot_name not in all_slots:
            return {"status": "FAILED",
                    "error-message": f"Slot '{slot_name}' not found (valid: {all_slots})"}

        try:
            for slot in all_slots:
                xpath = f"/o-ran-software-management:software-inventory/software-slot[name='{slot}']/active"
                self.netconf.set_data(
                    Datastore.OPERATIONAL, xpath, "true" if slot == slot_name else "false"
                )
            import json
            with open("/data/.sw-activation-state.json", "w") as f:
                json.dump({"activated_slot": slot_name}, f)
        except Exception as e:
            return {"status": "FAILED", "error-message": f"Failed to update slot states: {e}"}

        def send_activation_notification():
            time.sleep(1)
            try:
                self.netconf.running.notification_send(
                    "/o-ran-software-management:activation-event",
                    {"slot-name": slot_name, "status": "COMPLETED"},
                )
                logger.info(f"Sent activation-event notification: slot={slot_name}, COMPLETED")
            except Exception as e:
                logger.error(f"Failed to send activation notification: {e}")

        threading.Thread(target=send_activation_notification, daemon=True).start()
        return {"status": "STARTED", "notification-timeout": 30}

    # -------------------------------------------------------------------------
    # software-download (real sFTP GET)
    # -------------------------------------------------------------------------

    def _handle_software_download_rpc(self, rpc_path, input_params, event, private_data):
        logger.info(f"Received software-download RPC: {input_params}")

        remote_file_path = None
        password = ""
        if input_params:
            remote_file_path = input_params.get(
                "remote-file-path",
                input_params.get("o-ran-software-management:remote-file-path"),
            )
            pw_block = input_params.get("password") or {}
            if isinstance(pw_block, dict):
                password = pw_block.get("password", "")
            else:
                password = str(pw_block)

        if not remote_file_path:
            return {"status": "FAILED", "error-message": "Missing remote-file-path"}

        self.last_download_path = remote_file_path

        def perform_download():
            status, local_file = self._perform_sftp_download(remote_file_path, password)
            if status == "COMPLETED":
                self.last_local_file = local_file
            try:
                self.netconf.running.notification_send(
                    "/o-ran-software-management:download-event",
                    {"file-name": remote_file_path, "status": status},
                )
                logger.info(f"Sent download-event notification: {status}")
            except Exception as e:
                logger.error(f"Failed to send download notification: {e}")

        threading.Thread(target=perform_download, daemon=True).start()
        return {"status": "STARTED", "notification-timeout": 30}

    def _perform_sftp_download(self, remote_file_path: str, password: str):
        """
        Download the software package from ``remote_file_path`` via paramiko sFTP.

        Returns a ``(status, local_path)`` tuple where ``status`` is one of:
        COMPLETED, AUTHENTICATION_ERROR, FILE_NOT_FOUND, PROTOCOL_ERROR, TIMEOUT,
        APPLICATION_ERROR. On failure, ``local_path`` is an empty string.
        """
        url = urlparse(remote_file_path.strip())
        host = url.hostname
        port = url.port or 22
        user = url.username or ""
        remote_path = url.path.lstrip("/")
        if not host:
            return "APPLICATION_ERROR", ""

        local_file = os.path.join(self.DOWNLOAD_CACHE, os.path.basename(remote_path) or "pkg.zip")
        transport = None
        try:
            transport = paramiko.Transport(
                (host, port),
                disabled_algorithms=_SPS_4_1_DISABLED_ALGORITHMS,
            )
            transport.banner_timeout = 15
            transport.connect(username=user, password=password)
            with paramiko.SFTPClient.from_transport(transport) as sftp:
                sftp.get(remote_path, local_file)
            logger.info(
                f"sFTP download succeeded: {host}:{port}/{remote_path} -> {local_file}"
            )
            return "COMPLETED", local_file
        except paramiko.AuthenticationException:
            logger.error(f"sFTP authentication failed for {user}@{host}:{port}")
            return "AUTHENTICATION_ERROR", ""
        except FileNotFoundError:
            logger.error(f"sFTP remote file not found: {remote_path}")
            return "FILE_NOT_FOUND", ""
        except IOError as e:
            # paramiko raises IOError for SFTP 'no such file'
            if "No such file" in str(e) or getattr(e, "errno", None) == 2:
                logger.error(f"sFTP no-such-file: {remote_path}")
                return "FILE_NOT_FOUND", ""
            logger.error(f"sFTP IO error: {e}")
            return "APPLICATION_ERROR", ""
        except socket.timeout:
            logger.error("sFTP socket timeout")
            return "TIMEOUT", ""
        except paramiko.SSHException as e:
            logger.error(f"sFTP protocol error: {e}")
            return "PROTOCOL_ERROR", ""
        except (socket.gaierror, OSError) as e:
            logger.error(f"sFTP network error: {e}")
            return "APPLICATION_ERROR", ""
        except Exception as e:
            logger.error(f"sFTP unexpected error: {e}")
            return "APPLICATION_ERROR", ""
        finally:
            if transport is not None:
                try:
                    transport.close()
                except Exception:
                    pass

    # -------------------------------------------------------------------------
    # software-install (real zip + manifest validation)
    # -------------------------------------------------------------------------

    def _handle_software_install_rpc(self, rpc_path, input_params, event, private_data):
        logger.info(f"Received software-install RPC: {input_params}")
        if not input_params:
            return {"status": "FAILED", "error-message": "Missing input parameters"}

        slot_name = input_params.get(
            "slot-name", input_params.get("o-ran-software-management:slot-name")
        )
        if not slot_name:
            return {"status": "FAILED", "error-message": "Missing slot-name"}

        def perform_install():
            status = self._validate_package(self.last_local_file)
            if status == "COMPLETED":
                try:
                    xpath = (
                        f"/o-ran-software-management:software-inventory/"
                        f"software-slot[name='{slot_name}']/status"
                    )
                    self.netconf.set_data(Datastore.OPERATIONAL, xpath, "VALID")
                except Exception as e:
                    logger.debug(f"Could not update slot status: {e}")

            try:
                self.netconf.running.notification_send(
                    "/o-ran-software-management:install-event",
                    {"slot-name": slot_name, "status": status},
                )
                logger.info(f"Sent install-event notification: {status}")
            except Exception as e:
                logger.error(f"Failed to send install notification: {e}")

        threading.Thread(target=perform_install, daemon=True).start()
        return {"status": "STARTED", "notification-timeout": 30}

    def _validate_package(self, local_file: str) -> str:
        """
        Validate the previously downloaded package.

        Returns 'COMPLETED' on success, 'FILE_ERROR' if the archive cannot be read,
        'INTEGRITY_ERROR' if manifest.xml is missing/empty or the firmware payload is
        corrupt, 'APPLICATION_ERROR' for unexpected failures.
        """
        if not local_file or not os.path.exists(local_file):
            logger.error(f"No cached package to install: {local_file!r}")
            return "FILE_ERROR"
        try:
            with zipfile.ZipFile(local_file) as zf:
                names = zf.namelist()
                if "manifest.xml" not in names:
                    logger.error(f"Package missing manifest.xml: {local_file}")
                    return "INTEGRITY_ERROR"
                manifest_bytes = zf.read("manifest.xml")
                if not manifest_bytes.strip():
                    logger.error(f"Package has empty manifest.xml: {local_file}")
                    return "INTEGRITY_ERROR"
                # Validate the actual firmware payload, not just the manifest: a real O-RU
                # unpacks and integrity-checks the inner archive before reporting COMPLETED.
                # The package carries the firmware as a *.tar.bz2 member; if it is missing or
                # not a readable bz2 tarball (corrupt payload), install must fail.
                tar_members = [n for n in names if n.endswith(".tar.bz2")]
                if not tar_members:
                    logger.error(f"Package has no .tar.bz2 firmware payload: {local_file}")
                    return "INTEGRITY_ERROR"
                for tar_name in tar_members:
                    try:
                        with zf.open(tar_name) as payload:
                            with tarfile.open(fileobj=payload, mode="r:bz2") as tf:
                                tf.getmembers()  # forces decompression of the bz2 stream
                    except (tarfile.TarError, OSError, EOFError) as pe:
                        logger.error(
                            f"Package payload {tar_name} is not a valid bz2 tar "
                            f"(corrupt firmware): {pe}")
                        return "INTEGRITY_ERROR"
            logger.info(f"Package validated (manifest + payload): {local_file}")
            return "COMPLETED"
        except zipfile.BadZipFile:
            logger.error(f"Package is not a valid zip: {local_file}")
            return "FILE_ERROR"
        except Exception as e:
            logger.error(f"Unexpected install error: {e}")
            return "APPLICATION_ERROR"
