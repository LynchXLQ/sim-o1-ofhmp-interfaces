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

from scapy.all import Ether, IP, UDP, BOOTP, DHCP, sendp, sniff, AsyncSniffer, get_if_list
from typing import Optional
from util.logging import get_pynts_logger
import os
import sys
import time

from strenum import StrEnum
from util.docker import get_hostname, get_container_mac_address

logger = get_pynts_logger("config")

# Mapping of Option 43 sub-option types
OPTION_43_TYPES = {
    0x81: "Controller IP Address",
    0x82: "Controller FQDN",
    0x83: "Event Collector IP Address",
    0x84: "Event Collector FQDN",
    0x85: "PNF Registration Format",
    0x86: "NETCONF Call Home"
}

"""
Configuration class
Singleton
----
Builds config from:
- environment variables
- JSON config
- Netconf datastore
"""
class Config:
    _instance = None

    # environment variables
    network_function_type: str = "undefined"

    ssh_listen_endpoint: bool = True
    tls_listen_endpoint: bool = False
    ssh_callhome_endpoint: bool = False
    tls_callhome_endpoint: bool = False

    ssh_listen_port: int = 830      # default value, currently unmodifiable by env
    tls_listen_port: int = 6513     # default value, currently unmodifiable by env

    sftp_listen_port: int = 22      # default valuem currently unmodifiable by env

    netconf_username: str = "netconf"
    netconf_password: str = "netconf!"

    sdnr_username: str = "admin"
    sdnr_password: str = "admin"

    sdnr_restconf_url: str

    ves_url: str
    ves_username: str
    ves_password: str
    
    dhcp_sdnr_controller_ip: str
    dhcp_sdnr_fqdn: str
    dhcp_sdnr_callhome_tls: bool
    
    dhcp_ves_ip: str
    dhcp_ves_fqdn: str
    
    sdnr_certificate_markers: bool = False

    # json variables

    # netconf variables
    hostname: str = get_hostname()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.reload()
        return cls._instance

    def reload(self) -> None:
        logger.info("reloading config")
        
        self.dhcp_sdnr_controller_ip: str = None
        self.dhcp_sdnr_fqdn: str = None
        self.dhcp_sdnr_callhome_tls: bool = False
        
        self.dhcp_ves_ip: str = None
        self.dhcp_ves_fqdn: str = None

        self.netconf_function_type: str = os.environ.get("NETWORK_FUNCTION_TYPE", "undefined")

        self.ssh_listen_endpoint: bool = self.get_envvar_bool("SSH_LISTEN_ENDPOINT", "True")
        self.tls_listen_endpoint: bool = self.get_envvar_bool("TLS_LISTEN_ENDPOINT", "False")
        self.ssh_callhome_endpoint: bool = self.get_envvar_bool("SSH_CALLHOME_ENDPOINT", "False")
        self.tls_callhome_endpoint: bool = self.get_envvar_bool("TLS_CALLHOME_ENDPOINT", "False")

        # Only run DHCP discovery if we're in call-home mode (not listen mode)
        if self.ssh_callhome_endpoint or self.tls_callhome_endpoint:
            self.dhcp_get_config()
        else:
            logger.info("Skipping DHCP discovery (listen mode enabled)")

        endpoints = os.environ.get("ENDPOINT_COUNT", 1)
        try:
            self.endpoint_count: int = int(endpoints)
        except ValueError:
            logger.error(f"Got config ENDPOINT_COUNT=f{endpoints}, which is not integer. Defaulted to 1 endpoint count.")
            self.endpoint_count = 1

        self.netconf_username: str = os.environ.get("NETCONF_USERNAME", "netconf")
        self.netconf_password: str = os.environ.get("NETCONF_PASSWORD", "netconf!")

        self.sdnr_restconf_url: str = os.environ.get("SDNR_RESTCONF_URL", "")
        self.sdnr_username: str = os.environ.get("SDNR_USERNAME", "admin")
        self.sdnr_password: str = os.environ.get("SDNR_PASSWORD", "admin")

        if self.dhcp_ves_fqdn is not None:
          self.ves_url = f"https://{self.dhcp_ves_fqdn}/eventListener/v7"
        elif self.dhcp_ves_ip is not None:
          self.ves_url = f"https://{self.dhcp_ves_ip}/eventListener/v7"
        else:
          self.ves_url = os.environ.get("VES_URL", "")
        self.ves_username = os.environ.get("VES_USERNAME", "sample1")
        self.ves_password = os.environ.get("VES_PASSWORD", "sample1")
        
        self.sdnr_certificate_markers: bool = self.get_envvar_bool("SDNR_CERTIFICATE_MARKERS", "False")

    @staticmethod
    def get_envvar_bool(varname: str, default_value: str) -> bool:
        truthy_values = {'true', '1', 't', 'y', 'yes'}

        value = os.environ.get(varname, default_value)

        return value.lower() in truthy_values

    def is_tls_enabled(self) -> bool:
        return self.tls_listen_endpoint or self.tls_callhome_endpoint

    # def only_one_connection_type(self) -> bool:
    #     only_one_listen_true = sum([self.ssh_listen_endpoint, self.tls_listen_endpoint]) == 2
    #     only_one_callhome_true = sum([self.ssh_callhome_endpoint, self.tls_callhome_endpoint]) == 2
    #     if only_one_listen_true or only_one_callhome_true:
    #         logger.error(f"Expecting only one type of connection (either SSH or TLS, but not both) at the same time! Got SSH_ENDPOINT={self.ssh_listen_endpoint}, TLS_ENDPOINT={self.tls_listen_endpoint}, SSH_CALLHOME={self.ssh_callhome_endpoint} and TLS_CALLHOME={self.tls_callhome_endpoint}. Please re-check the config!")
    #         sys.exit("Invalid configuration for the SSH and TLS endpoints.")
    
    def to_dict(self) -> dict:
        return {
            "ssh_listen_endpoint": self.ssh_listen_endpoint,
            "tls_listen_endpoint": self.tls_listen_endpoint,

            "ssh_callhome_endpoint": self.ssh_callhome_endpoint,
            "tls_callhome_endpoint": self.tls_callhome_endpoint,

            "endpoint_count": self.endpoint_count,

            "netconf_username": self.netconf_username,
            "netconf_password": self.netconf_password,

            "sdnr_restconf_url": self.sdnr_restconf_url,
            "sdnr_username": self.sdnr_username,
            "sdnr_password": self.sdnr_password,        
            
            "ves_url": self.ves_url,
            "ves_username": self.ves_username,
            "ves_password": self.ves_password
        }
        
    def parse_option_43(self, raw_bytes):
      """Parses vendor-encapsulated Option 43 data."""
      index = 0

      while index < len(raw_bytes):
          if index + 2 > len(raw_bytes):
              print("[!] Malformed Option 43 data (truncated)")
              break

          opt_type = raw_bytes[index]  # First byte is the sub-option type
          opt_len = raw_bytes[index + 1]  # Second byte is the length

          if index + 2 + opt_len > len(raw_bytes):
              print(f"[!] Skipping invalid Option 43 sub-option {opt_type:#04x} (bad length)")
              break

          opt_value = raw_bytes[index + 2: index + 2 + opt_len]  # Value field

          if opt_type == 0x81:  # IP Address (4 bytes)
            if opt_len == 4:
              parsed_value = ".".join(str(b) for b in opt_value)
              self.dhcp_sdnr_controller_ip = parsed_value
              logger.debug(f"Received Controller IP {self.dhcp_sdnr_controller_ip} via DHCP.")
            else:
              logger.error(f"Length of IP address is not 4, but {opt_len}. Failed to extract Controller IP address from DHCP.")
              self.dhcp_sdnr_controller_ip = None
                  
          elif opt_type == 0x83:  # IP Address (4 bytes)
              if opt_len == 4:
                parsed_value = ".".join(str(b) for b in opt_value)
                self.dhcp_ves_ip = parsed_value
                logger.debug(f"Received VES Collector IP {self.dhcp_ves_ip} via DHCP.")
              else:
                logger.error(f"Length of IP address is not 4, but {opt_len}. Failed to extract VES Collector IP address from DHCP.")
                self.dhcp_ves_ip = None
          
          elif opt_type == 0x82:  # FQDN (ASCII string)
            try:
              self.dhcp_sdnr_fqdn = opt_value.decode("ascii")
              logger.debug(f"Received Controller FQDN {self.dhcp_sdnr_fqdn} via DHCP.")
            except UnicodeDecodeError:
              logger.error(f"Could not decode Controller FQDN in ASCII. Received hex value: {opt_value.hex()}")
              self.dhcp_sdnr_fqdn = None
              
          elif opt_type == 0x84:  # FQDN (ASCII string)
            try:
              self.dhcp_ves_fqdn = opt_value.decode("ascii")
              logger.debug(f"Received VES Collector FQDN {self.dhcp_ves_fqdn} via DHCP.")
            except UnicodeDecodeError:
              logger.error(f"Could not decode Controller FQDN in ASCII. Received hex value: {opt_value.hex()}")
              self.dhcp_ves_fqdn = None

          elif opt_type == 0x86:  # Single-byte flag
            if opt_len == 1:
              parsed_value = int(opt_value[0])
              if parsed_value == 0:
                self.dhcp_sdnr_callhome_tls = False
                logger.debug(f"Received CallHome over SSH via DHCP.")
              elif parsed_value == 1:
                logger.debug(f"Received CallHome over TLS via DHCP.")
                self.dhcp_sdnr_callhome_tls = True
            else:
              logger.error(f"Could not get correct NETCONF Call Home information. Received hex value: {opt_value.hex()}")
          index += 2 + opt_len  # Move to the next sub-option

    def print_dhcp_options(self, dhcp_options):
      """Print all DHCP options from a list of (option, value) tuples."""
      for opt in dhcp_options:
        if isinstance(opt, tuple):
          if opt[0] == "vendor_specific":  # Fix: Use the correct Scapy name for Option 43
            logger.debug("    Option 43 (Vendor-Specific Information):")
            self.parse_option_43(opt[1])  # Convert raw bytes
          else:
            logger.debug(f"    Option {opt[0]}: {opt[1]}")

    def handle_packet(self, pkt):
      """Callback to process incoming DHCP packets and detect DHCPOFFER."""
      if DHCP in pkt:
        dhcp_opts = pkt[DHCP].options
        for opt in dhcp_opts:
          if isinstance(opt, tuple) and opt[0] == "message-type":
            if opt[1] in [2, "offer"]:  # DHCPOFFER detected
              server_ip = pkt[IP].src
              offered_ip = pkt[BOOTP].yiaddr
              logger.debug(f"[+] Received DHCPOFFER from {server_ip}")
              logger.debug(f"    Offered IP: {offered_ip}")
              logger.debug("    Full DHCP options:")
              self.print_dhcp_options(dhcp_opts)
              return True
      return False

    def dhcp_get_config(self):
        # Start sniffing in *async* mode so we don't block.
        for iface in get_if_list():
          if iface == 'lo':
            logger.debug(f"Skipping sending DHCPDISCOVER on {iface}...")
            continue
          logger.debug(f"Sending DHCPDISCOVER on {iface}")
          # sendp(discover, iface=iface, verbose=False)
          sniff_thread = AsyncSniffer(
              iface=iface,
              filter="udp and (port 67 or port 68)",
              prn=self.handle_packet,
              store=True  # Ensure packets are stored in results
          )
          sniff_thread.start()

          time.sleep(1)  # Give it a moment to get ready
        
          mac_addr = get_container_mac_address()
          logger.debug(f"Got back MAC address {mac_addr}")

          # Now send DHCPDISCOVER
          discover = (
              Ether(src=mac_addr, dst="ff:ff:ff:ff:ff:ff")
              / IP(src="0.0.0.0", dst="255.255.255.255")
              / UDP(sport=68, dport=67)
              / BOOTP(chaddr=b'\x02\x50\x02\x99\x00\x01', xid=0x99999999, flags=0x8000)
              / DHCP(options=[
                  ("message-type", "discover"),
                  ("parameter-request-list", [43]),  # Explicitly request Option 43
                  ("vendor_class_id", "o-ran-ru2/pynts"),  # Option 60
                  "end"
              ])
          )
          logger.debug("[*] Sending DHCPDISCOVER...")
          try:
              sendp(discover, iface=iface, verbose=False)
          except OSError as e:
              logger.warning(f"Skipping interface {iface}: {e}")
              try:
                  sniff_thread.stop()
              except Exception:
                  pass  # Sniffer may not be properly started, ignore
              continue

          logger.debug("[*] Sniffing for 5 seconds...")
          time.sleep(5)

          # sniff_thread.running = False
          sniff_thread.stop()
          results = sniff_thread.results
          logger.debug(f"[+] Captured {len(results)} packets in total")

        # for pkt in results:
        #     if self.handle_packet(pkt):
        #         logger.debug("[+] Test SUCCESS - Received a valid DHCPOFFER.")
        #         return True

        # logger.debug("[-] Test FAILED - No DHCPOFFER received.")
        # return False
