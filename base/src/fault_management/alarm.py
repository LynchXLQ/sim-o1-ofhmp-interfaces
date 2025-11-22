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
from datetime import datetime
from util.datetime import yang_datetime_to_datetime, datetime_to_yang_datetime, datetime_utcnow
from strenum import StrEnum

logger = get_pynts_logger("alarm")

class PerceivedSeverity(StrEnum):
    CLEARED = "cleared"
    # indeterminate falls back to WARNING on o-ran-fm
    INDETERMINATE = "indeterminate"
    WARNING = "warning"
    MINOR = "minor"
    MAJOR = "major"
    CRITICAL = "critical"

class Alarm:
    # combined id
    c_id: str
    _times_raised: int
    _times_cleared: int

    resource: str
    alarm_type_id: str
    alarm_type_qualifier: str

    is_cleared: bool
    perceived_severity: PerceivedSeverity

    time_created: datetime
    last_raised: datetime
    last_changed: datetime

    alarm_text: str

    def __init__(self) -> None:
        raise Exception("Can't create an empty alarm.")

    def __init__(self, resource: str, alarm_type_id: str, alarm_type_qualifier: str):
        self.resource = resource
        self.alarm_type_id = alarm_type_id
        self.alarm_type_qualifier = alarm_type_qualifier
        self.c_id = self.resource + self.alarm_type_id + self.alarm_type_qualifier
        self._times_raised = 0
        self._times_cleared = 0

        from fault_management.fault_management import FaultManagement
        self.fault_management: FaultManagement = FaultManagement()

    def alarm_raise(self, severity:PerceivedSeverity|None = None) -> None:
        if self.is_cleared:
            if severity is not None:
                if severity == PerceivedSeverity.CLEARED:
                    raise ValueError("Can't raise an alarm with CLEARED severity.")
                self.perceived_severity = severity
            self.is_cleared = False
            self.last_changed = datetime_utcnow()
            self.last_raised = self.last_changed

            self._times_raised = self._times_raised + 1

            self.fault_management.on_alarm_change(self)

    def alarm_clear(self) -> None:
        if not self.is_cleared:
            self.is_cleared = True
            self.last_changed = datetime_utcnow()

            self._times_cleared = self._times_cleared + 1

            self.fault_management.on_alarm_change(self)

    def alarm_notify(self) -> None:
        self.fault_management.send_notification(self)

    def counters_get(self) -> dict:
        return {"raised": self._times_raised, "cleared": self._times_cleared}

    def counters_clear(self) -> None:
        self._times_raised = 0
        self._times_cleared = 0


    @staticmethod
    def from_ietf_alarm(data: dict):
        alarm = Alarm(data["resource"], data["alarm-type-id"], data["alarm-type-qualifier"])

        a = alarm.fault_management.get_alarm(alarm.c_id)
        if a is None:
            alarm.fault_management.add_alarm(alarm)
        else:
            alarm = a

        alarm.is_cleared = data["is-cleared"]
        alarm.perceived_severity = PerceivedSeverity(data["perceived-severity"])
        alarm.alarm_text = data["alarm-text"]

        if "time-created" in data and "last-raised" in data and "last-changed" in data:
            alarm.time_created = yang_datetime_to_datetime(data["time-created"])
            alarm.last_raised = yang_datetime_to_datetime(data["last-raised"])
            alarm.last_changed = yang_datetime_to_datetime(data["last-changed"])
        else:
            alarm_time = datetime_utcnow()
            alarm.time_created = alarm_time
            alarm.last_raised = alarm_time
            alarm.last_changed = alarm_time

            if alarm.is_cleared:
                logger.warn(f"Alarm with id {alarm.c_id} was created as cleared.")


        if alarm.is_cleared:
            alarm._times_cleared = alarm._times_cleared + 1
        else:
            alarm._times_raised = alarm._times_raised + 1

        alarm.fault_management.on_alarm_change(alarm)
        return alarm


    @staticmethod
    def from_ietf_alarm_notif(notif: dict):
        alarm = Alarm(notif["resource"], notif["alarm-type-id"], notif["alarm-type-qualifier"])

        alarm_time = datetime_utcnow()
        if "time" in notif:
            alarm_time = yang_datetime_to_datetime(notif["time"])

        a = alarm.fault_management.get_alarm(alarm.c_id)
        if a is None:
            alarm.time_created = alarm_time
            alarm.last_raised = alarm_time
            alarm.fault_management.add_alarm(alarm)
        else:
            alarm = a

        if "perceived-severity" in notif:
            alarm.is_cleared = False
            perceived_severity = PerceivedSeverity(notif["perceived-severity"])
            if perceived_severity == PerceivedSeverity.CLEARED:
                if alarm.perceived_severity is None:
                    alarm.perceived_severity = PerceivedSeverity.INDETERMINATE
                alarm.is_cleared = True
            else:
                alarm.perceived_severity = perceived_severity
                alarm.last_raised = alarm_time

        if "is-cleared" in notif:
            alarm.is_cleared = notif["is-cleared"]

        if "alarm-text" in notif:
            alarm.alarm_text = notif["alarm-text"]

        alarm.last_changed = alarm_time

        if alarm.is_cleared:
            alarm._times_cleared = alarm._times_cleared + 1
        else:
            alarm._times_raised = alarm._times_raised + 1


        alarm.fault_management.on_alarm_change(alarm)
        return alarm

    def to_ietf_alarm(self) -> dict:
        data = {}
        data["resource"] = self.resource
        data["alarm-type-id"] = self.alarm_type_id
        data["alarm-type-qualifier"] = self.alarm_type_qualifier

        data["is-cleared"] = self.is_cleared
        data["perceived-severity"] = str(self.perceived_severity)

        data["time-created"] = datetime_to_yang_datetime(self.time_created)
        data["last-raised"] = datetime_to_yang_datetime(self.last_raised)
        data["last-changed"] = datetime_to_yang_datetime(self.last_changed)

        data["alarm-text"] = self.alarm_text

        return data



    def to_ietf_alarm_notif(self) -> dict:
        data = {}
        data["resource"] = self.resource
        data["alarm-type-id"] = self.alarm_type_id
        data["alarm-type-qualifier"] = self.alarm_type_qualifier

        if self.is_cleared:
            data["perceived-severity"] = PerceivedSeverity.CLEARED
        else:
            data["perceived-severity"] = str(self.perceived_severity)

        data["time"] = datetime_to_yang_datetime(self.last_changed)

        data["alarm-text"] = self.alarm_text

        return data




    # ietf-alarms                                         o-ran-fm

    # resource: str                       fault-source: str
    # alarm_type_id: str                  alarm_type: str ... e de fapt enum
    # alarm_type_qual: str                fault_id: uint16

    # is_cleared: bool                    is_cleared: bool
    # perceived_severity: enum            fault_severty: enum

    # time_created: datettime             ... se pot scoate din cod
    # last_raised: datettime              ... se pot scoate din cod
    # last_changed: datettime             event_time: datetime

    # alarm_text: str                     fault_text: str



    @staticmethod
    def from_oran_fm(data: dict):
        """
        Create Alarm object from O-RAN FM format data.

        Maps O-RAN FM fields to internal Alarm representation:
        - fault-source → resource
        - fault-id → alarm_type_qualifier
        - fault-severity → perceived_severity
        - is-cleared → is_cleared
        - fault-text → alarm_text
        - event-time → last_changed
        """
        from fault_management.fault_management import FaultManagement

        # Map fault-id to alarm_type_qualifier, use default alarm type
        fault_id = str(data["fault-id"])
        fault_source = data["fault-source"]
        alarm_type = data.get("alarm-type", "PROCESSING-ERROR-ALARM")

        # Create alarm with required fields
        alarm = Alarm(fault_source, alarm_type, fault_id)

        # Set state fields
        alarm.is_cleared = data.get("is-cleared", False)

        # Map severity - handle both O-RAN FM format (uppercase) and internal format
        severity_str = data.get("fault-severity", "WARNING").lower()
        alarm.perceived_severity = PerceivedSeverity(severity_str)

        # Set descriptive text
        alarm.alarm_text = data.get("fault-text", "")

        # Handle timestamps
        if "event-time" in data:
            alarm.last_changed = yang_datetime_to_datetime(data["event-time"])
        else:
            alarm.last_changed = datetime_utcnow()

        # Set creation time if not raised before
        alarm.time_created = alarm.last_changed
        alarm.last_raised = alarm.last_changed if not alarm.is_cleared else alarm.time_created

        # Register alarm with fault management if not already present
        fm = FaultManagement()
        existing = fm.get_alarm(alarm.c_id)
        if existing is None:
            fm.add_alarm(alarm)
            logger.info(f"Added alarm from O-RAN FM data: {alarm.c_id}")
        else:
            # Update existing alarm
            alarm = existing
            alarm.is_cleared = data.get("is-cleared", False)
            alarm.perceived_severity = PerceivedSeverity(severity_str)
            alarm.alarm_text = data.get("fault-text", "")
            logger.info(f"Updated existing alarm: {alarm.c_id}")

        return alarm

    def to_oran_fm(self) -> dict:
        """
        Convert internal Alarm to O-RAN FM format.

        Returns dict with all required O-RAN FM alarm fields:
        - fault-id, fault-source, fault-severity, is-cleared,
        - fault-text, event-time, affected-objects
        """
        # Map severity to O-RAN FM format (uppercase)
        severity_map = {
            PerceivedSeverity.CLEARED: "CLEARED",
            PerceivedSeverity.INDETERMINATE: "WARNING",  # O-RAN FM doesn't have indeterminate
            PerceivedSeverity.WARNING: "WARNING",
            PerceivedSeverity.MINOR: "MINOR",
            PerceivedSeverity.MAJOR: "MAJOR",
            PerceivedSeverity.CRITICAL: "CRITICAL"
        }

        severity_oran = severity_map.get(self.perceived_severity, "WARNING")

        # Build O-RAN FM alarm dict
        oran_alarm = {
            "fault-id": int(self.alarm_type_qualifier),
            "fault-source": self.resource,
            "fault-severity": severity_oran,
            "is-cleared": self.is_cleared,
            "fault-text": self.alarm_text,
            "event-time": datetime_to_yang_datetime(self.last_changed)
        }

        # Add affected-objects if resource contains component info
        # Extract component name from resource (e.g., "o-ran-hardware" → "hardware")
        component_name = self.resource.split(":")[-1] if ":" in self.resource else self.resource
        oran_alarm["affected-objects"] = [{"name": component_name}]

        return oran_alarm

    @staticmethod
    def from_oran_fm_notif(notif: dict):
        """Create Alarm from O-RAN FM notification format."""
        return Alarm.from_oran_fm(notif)

    def to_oran_fm_notif(self) -> dict:
        """Convert Alarm to O-RAN FM notification format."""
        return self.to_oran_fm()
