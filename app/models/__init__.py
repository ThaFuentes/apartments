"""Apt MariaDB models grouped by product domain."""

from app.builddb.builddb import db
from app.services.clock import utcnow

_OPTS = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_unicode_ci"}
from app.models.people import User, AssistantProfile, ApiCredential, ApiUsage, PropertyAccess, Region, RegionAccess, RegionCity, UserCapability, RoleCapabilityDefault, UserHat, CustomRole
from app.models.places import City, Property, Unit, UnitTask, UnitChange
from app.models.work import Trip, TripProperty, PlanItem, Shift, UnitVisit, Job, JobEvent, Equipment, EquipmentTemplate, EquipmentMove, OdometerReading, MilesEntry, MileageLeg
from app.models.reports import Media, Expense, Report, ReportShare
from app.models.system import LocationPing, AuditLog, PendingAction, IdempotencyKey, ChatMessage, Notice, AppSetting

__all__ = ['db', 'utcnow', '_OPTS', 'User', 'AssistantProfile', 'ApiCredential', 'ApiUsage', 'PropertyAccess', 'Region', 'RegionAccess', 'RegionCity', 'UserCapability', 'RoleCapabilityDefault', 'UserHat', 'CustomRole', 'City', 'Property', 'Unit', 'UnitTask', 'UnitChange', 'Trip', 'TripProperty', 'PlanItem', 'Shift', 'UnitVisit', 'Job', 'JobEvent', 'Equipment', 'EquipmentTemplate', 'EquipmentMove', 'OdometerReading', 'MilesEntry', 'MileageLeg', 'Media', 'Expense', 'Report', 'ReportShare', 'LocationPing', 'AuditLog', 'PendingAction', 'IdempotencyKey', 'ChatMessage', 'Notice', 'AppSetting']
