"""Import and expose the Apt route blueprint and legacy handler aliases."""
from app.routes.common import bp
from app.routes import auth as _auth_routes
from app.routes import fieldwork as fieldwork
from app.routes import property_units as property_units
from app.routes import admin as admin
from app.routes import reports as reports
from app.routes import location as location
from app.routes import audit as audit
from app.routes import regions as regions
from app.routes import security as security
from app.routes import ready as ready
from app.routes import upkeep as upkeep

# Preserve imports some local tools may use while routes live in focused modules.
from app.routes.fieldwork import home, add_site, plan_day, chat, trips
from app.routes.property_units import _editable_unit, _equipment_form
from app.routes.location import _photo_proposal
