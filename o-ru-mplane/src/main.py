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

import requests
import threading

from core.dict_factory import DictFactory, BaseTemplate
from core.extension import Extension
from core.config import Config
from core.netconf import Netconf, Datastore
from util.crypto import CryptoUtils
from util.threading import sa_sleep
from util.logging import get_pynts_logger
from sysrepo.errors import SysrepoNotFoundError
from feature.o_ran_supervision import ORanSupervisionFeature
from feature.o_ran_operations import ORanOperationsFeature
from feature.o_ran_software_management import ORanSoftwareManagementFeature
from feature.o_ran_troubleshooting import ORanTroubleshootingFeature
from feature.o_ran_trace import ORanTraceFeature
from feature.o_ran_file_management import ORanFileManagementFeature
from feature.o_ran_energy_saving import ORanEnergySavingFeature
from feature.o_ran_uplane_conf import ORanUplaneConfFeature
from feature.o_ran_udp_echo import ORanUdpEchoFeature
from feature.o_ran_epe_statistics import ORanEpeStatisticsFeature
from feature.o_ran_usermgmt import ORanUsermgmtFeature

logger = get_pynts_logger("o-ru-mplane")

ODL_CALLHOME_ALLOW_DEVICES_URL="/rests/data/odl-netconf-callhome-server:netconf-callhome-server/allowed-devices/device="
ODL_ADD_TRUSTED_CERT_URL="/rests/operations/netconf-keystore:add-trusted-certificate"
ODL_RM_TRUSTED_CERT_URL="/rests/operations/netconf-keystore:remove-trusted-certificate"
HTTP_YANG_JSON_HEADERS = {
                'content-type': 'application/yang-data+json',
                'accept': 'application/yang-data+json'
            }
HTTP_JSON_HEADERS = {
                'content-type': 'application/json',
                'accept': 'application/json'
            }

class Main(Extension):
    def init(self) -> None:
        self.netconf = Netconf()
        self.config = Config()
        self.crypto_util = CryptoUtils()
        self.supervision = ORanSupervisionFeature()
        self.operations = ORanOperationsFeature()
        self.software_management = ORanSoftwareManagementFeature()
        self.troubleshooting = ORanTroubleshootingFeature()
        self.trace = ORanTraceFeature()
        self.file_management = ORanFileManagementFeature()
        self.energy_saving = ORanEnergySavingFeature()
        self.uplane_conf = ORanUplaneConfFeature()
        self.udp_echo = ORanUdpEchoFeature()
        self.epe_statistics = ORanEpeStatisticsFeature()
        self.usermgmt = ORanUsermgmtFeature()

        DictFactory.add_template("o-ran-certificates", OranCertificatesTemplate)
        DictFactory.add_template("odl-netconf-callhome-server-ssh", OdlNetconfCallhomeServerSshTemplate)
        DictFactory.add_template("odl-netconf-callhome-server-tls", OdlNetconfCallhomeServerTlsTemplate)
        DictFactory.add_template("odl-netconf-callhome-trusted-cert", OdlNetconfCallhomeTrustedCertificateTemplate)
        DictFactory.add_template("odl-netconf-callhome-trusted-cert-remove", OdlNetconfCallhomeRemoveTrustedCertificateTemplate)
        

    def startup(self) -> None:
        self.update_o_ran_certificates()
        is_tls = self.replace_callhome_settings()
        self.start_odl_allow_thread(is_tls)
        self.supervision.start()
        self.operations.start()
        self.software_management.start()
        self.troubleshooting.start()
        self.trace.start()
        self.file_management.start()
        self.energy_saving.start()
        self.uplane_conf.start()
        self.udp_echo.start()
        self.epe_statistics.start()
        self.usermgmt.start()
        logger.info("o-ru-mplane extension loaded")

    def update_o_ran_certificates(self) -> None:
        o_ran_certificates_template = DictFactory.get_template("o-ran-certificates")
        o_ran_certificates_template.update_key(["o-ran-certificates", "certificate-parameters", "cert-maps", "cert-to-name", 0, "fingerprint"], self.crypto_util.get_certificate_fingerprint(self.crypto_util.root_odu_ca_cert))

        self.netconf.set_data(Datastore.RUNNING, "", o_ran_certificates_template.data)
        self.netconf.set_data(Datastore.OPERATIONAL, "", o_ran_certificates_template.data)
        

    def start_odl_allow_thread(self, is_tls: bool):
      if is_tls is True:
        request_thread = threading.Thread(target=self.send_odl_callhome_allow_tls)
      elif is_tls is False:
        request_thread = threading.Thread(target=self.send_odl_callhome_allow_ssh)
      request_thread.daemon = True  # Set as daemon so it exits when the main program exits
      request_thread.start()
    
    def send_odl_callhome_allow_tls(self) -> None:
        odl_trusted_cert_template = DictFactory.get_template("odl-netconf-callhome-trusted-cert")
        odl_trusted_cert_template.update_key(["input", "trusted-certificate", 0, "name"], self.config.hostname)
        odl_trusted_cert_template.update_key(["input", "trusted-certificate", 0, "certificate"], self.crypto_util.get_certificate_base64_encoding(is_smo=True, with_markers=self.config.sdnr_certificate_markers))
        
        odl_trusted_cert_template_remove = DictFactory.get_template("odl-netconf-callhome-trusted-cert-remove")
        odl_trusted_cert_template_remove.update_key(["input","name", 0], self.config.hostname)

        allow_tls_template = DictFactory.get_template("odl-netconf-callhome-server-tls")
        allow_tls_template.update_key(["odl-netconf-callhome-server:device", "unique-id"], self.config.hostname)
        allow_tls_template.update_key(["odl-netconf-callhome-server:device", "tls-client-params", "certificate-id"], self.config.hostname)

        success1 = False  # Flag to track the success of the first request
        success2 = False  # Flag to track the success of the second request
        while not (success1 and success2):
          if not success1:
            try:              
              # Trying to delete first the cert
              url = self.config.sdnr_restconf_url + ODL_RM_TRUSTED_CERT_URL
              payload = odl_trusted_cert_template_remove.data
              logger.debug(f"sending HTTP POST to {url} with payload {payload}")
              response = requests.post(url, auth=(self.config.sdnr_username, self.config.sdnr_password), json=payload, headers=HTTP_YANG_JSON_HEADERS, verify=False)
              if response.status_code >= 200 and response.status_code < 300:
                logger.debug(f"HTTP response to {url} succeded with code {response.status_code}")
              else:
                logger.error(f"HTTP POST request failed to {url} with payload {payload} with status_code={response.status_code}")             
            except requests.RequestException as e:
                logger.error(f"Error occurred in first request: {e}. Retrying in 10 seconds...")
            
            try:                                          
              url = self.config.sdnr_restconf_url + ODL_ADD_TRUSTED_CERT_URL
              payload = odl_trusted_cert_template.data
              logger.debug(f"sending HTTP POST to {url} with payload {payload}")
              response = requests.post(url, auth=(self.config.sdnr_username, self.config.sdnr_password), json=payload, headers=HTTP_YANG_JSON_HEADERS, verify=False)
              if response.status_code >= 200 and response.status_code < 300:
                logger.debug(f"HTTP response to {url} succeded with code {response.status_code}")
                success1 = True
              else:
                logger.error(f"HTTP POST request failed to {url} with payload {payload} with status_code={response.status_code}")
            except requests.RequestException as e:
                logger.error(f"Error occurred in first request: {e}. Retrying in 10 seconds...")

          if not success2:
            try:              
              url = self.config.sdnr_restconf_url + ODL_CALLHOME_ALLOW_DEVICES_URL + self.config.hostname
              payload = allow_tls_template.data
              logger.debug(f"sending HTTP PUT to {url} with payload {payload}")
              response = requests.put(url, auth=(self.config.sdnr_username, self.config.sdnr_password), json=payload, headers=HTTP_YANG_JSON_HEADERS, verify=False)
              if response.status_code >= 200 and response.status_code < 300:
                logger.debug(f"HTTP response to {url} succeded with code {response.status_code}")
                success2 = True
              else:
                  logger.error(f"HTTP PUT request failed to {url} with payload {payload} with status_code={response.status_code}")
            except requests.RequestException as e:
                logger.error(f"Error occurred in second request: {e}. Retrying in 10 seconds...")
          
          # Wait 10 seconds before retrying
          if not (success1 and success2):
              sa_sleep(10)

    def send_odl_callhome_allow_ssh(self) -> None:
        allow_ssh_template = DictFactory.get_template("odl-netconf-callhome-server-ssh")

        allow_ssh_template.update_key(["odl-netconf-callhome-server:device", "unique-id"], self.config.hostname)
        allow_ssh_template.update_key(["odl-netconf-callhome-server:device", "ssh-client-params", "credentials", "username"], self.config.netconf_username)
        allow_ssh_template.update_key(["odl-netconf-callhome-server:device", "ssh-client-params", "credentials", "passwords"], self.config.netconf_password, append_to_list=True)
        allow_ssh_template.update_key(["odl-netconf-callhome-server:device", "ssh-client-params", "host-key"], self.crypto_util.get_public_key_ssh_format())

        url = self.config.sdnr_restconf_url + ODL_CALLHOME_ALLOW_DEVICES_URL + self.config.hostname
        
        success1 = False  # Flag to track the success of the request
        while not success1:
          logger.debug(f"sending HTTP PUT to {url} with payload {allow_ssh_template.data}")
          response = requests.put(url, auth=(self.config.sdnr_username, self.config.sdnr_password), json=allow_ssh_template.data, headers=HTTP_YANG_JSON_HEADERS, verify=False)
          if response.status_code >= 200 and response.status_code < 300:
            logger.debug(f"HTTP response to {url} succeded with code {response.status_code}")
            success1 = True
          else:
            logger.error(f"HTTP PUT request failed to {url} with payload {allow_ssh_template.data} with status_code={response.status_code}")
            
          # Wait 10 seconds before retrying
          if not (success1):
              sa_sleep(10)

    def replace_callhome_settings(self) -> bool:
      is_tls = False
      try:
        client_parameters = self.netconf.running.get_data("/ietf-netconf-server:netconf-server/call-home/netconf-client/endpoints/endpoint/tls/tcp-client-parameters")
        is_tls = True
      except SysrepoNotFoundError as e:
        try:
          client_parameters = self.netconf.running.get_data("/ietf-netconf-server:netconf-server/call-home/netconf-client/endpoints/endpoint/ssh/tcp-client-parameters")
          is_tls = False
        except SysrepoNotFoundError as e:
            try:
                client_parameters = self.netconf.running.get_data(
                    "/ietf-netconf-server:netconf-server/listen/endpoints/endpoint/tls/tcp-client-parameters")
                is_tls = True
            except SysrepoNotFoundError as e:
                return is_tls
            return is_tls
      
      if self.config.dhcp_sdnr_fqdn is not None:        
        update_remote_address(client_parameters, "smo", self.config.dhcp_sdnr_fqdn)
      elif self.config.dhcp_sdnr_controller_ip is not None:
        update_remote_address(client_parameters, "smo", self.config.dhcp_sdnr_controller_ip)      
      else:
        # we don't change anything if we got nothing via DHCP
        return is_tls
      
      self.netconf.running.edit_batch(client_parameters, "ietf-netconf-server", default_operation="merge")
      self.netconf.running.apply_changes()
      
      return is_tls

     
def update_remote_address(config, target_endpoint_name, new_address):
  """Recursively update the 'remote-address' for a given endpoint name (just a substring comparison)."""
  if isinstance(config, dict):
      for key, value in config.items():
          if key == "endpoint" and isinstance(value, list):
              for endpoint in value:
                  if target_endpoint_name in endpoint.get("name"):
                      # Update the remote-address if the structure matches
                      if "tls" in endpoint and "tcp-client-parameters" in endpoint["tls"]:
                          endpoint["tls"]["tcp-client-parameters"]["remote-address"] = new_address
          else:
              update_remote_address(value, target_endpoint_name, new_address)
  elif isinstance(config, list):
      for item in config:
          update_remote_address(item, target_endpoint_name, new_address)

class OranCertificatesTemplate(BaseTemplate):
    """A dictionary template for netconf-server-parameters objects."""
    def create_dict(self):
        config = Config()
        return { "o-ran-certificates": {
                    "certificate-parameters": {
                        "cert-maps": {
                            "cert-to-name": [
                                {
                                    "id": 1,
                                    "fingerprint": "FINGERPRINT_TO_BE_REPLACED",
                                    "map-type": "ietf-x509-cert-to-name:san-rfc822-name"
                                }
                            ]
                        }
                    }
                }
              }

class OdlNetconfCallhomeServerSshTemplate(BaseTemplate):
    """A dictionary template for odl-netconf-callhome-server-ssh objects."""
    def create_dict(self):
        return {
                    "odl-netconf-callhome-server:device": {
                        "unique-id": "TO_BE_REPLACED_HOSTNAME",
                        "ssh-client-params": {
                            "credentials": {
                                "username": "TO_BE_REPLACED_USERNAME",
                                "passwords": [ ]
                            },
                            "host-key": "TO_BE_REPLACED_SSHKEY"
                        }
                    }
                }

class OdlNetconfCallhomeServerTlsTemplate(BaseTemplate):
    """A dictionary template for odl-netconf-callhome-server-tls objects."""
    def create_dict(self):
        return {
                    "odl-netconf-callhome-server:device": {
                        "unique-id": "TO_BE_REPLACED_HOSTNAME",
                        "tls-client-params": {
                            "key-id": "ODL_private_key_0",  # hardcoded ODL key here
                            "certificate-id": "TO_BE_REPLACED_HOSTNAME"
                        }
                    }
                }


class OdlNetconfCallhomeTrustedCertificateTemplate(BaseTemplate):
    """A dictionary template for odl-netconf-callhome-trusted-cert objects."""
    def create_dict(self):
        return {
                "input": {
                    "trusted-certificate": [
                    {
                        "name": "TO_BE_REPLACED_HOSTNAME",
                        "certificate": "TO_BE_REPLACED_SERVER_CERT"
                    }
                    ]
                }
            }

class OdlNetconfCallhomeRemoveTrustedCertificateTemplate(BaseTemplate):
    """A dictionary template for removing odl-netconf-callhome-trusted-cert objects."""
    def create_dict(self):
        return {
                "input": {                    
                        "name": ["TO_BE_REPLACED_HOSTNAME"]
                }
            }
