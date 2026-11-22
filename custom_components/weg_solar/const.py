"""Constants for the WEG Solar Portal integration."""

DOMAIN = "weg_solar"
MANUFACTURER = "WEG"
PORTAL_URL = "https://solarportal.weg.net"

# API
BASE_URL = "https://solarportal-api.weg.net/api/v1"
HEADER_API_KEY = "x-api-key"
HEADER_API_SECRET = "x-api-secret"

# Config entry data
CONF_API_KEY = "api_key"
CONF_API_SECRET = "api_secret"
CONF_PLANT_ID = "plant_id"
CONF_PLANT_NAME = "plant_name"

# Polling
UPDATE_INTERVAL = 300  # seconds
# Minimum spacing between two requests sharing the same API key (seconds).
MIN_REQUEST_INTERVAL = 2.0
# Alarms change slowly: refresh every N coordinator cycles.
ALARMS_EVERY_N_CYCLES = 3
# Periodic discovery of new plants linked to the same API key (seconds).
PLANT_DISCOVERY_INTERVAL = 3600

# Documented measurement variables
VAR_AC_ACTIVE_POWER = "acActivePower"
VAR_OUTPUT_ENERGY = "outputEnergy"

# groupBy values in milliseconds (see API docs, "Limites de groupBy")
GROUP_BY_10_MIN = 600_000
GROUP_BY_15_MIN = 900_000
GROUP_BY_1_HOUR = 3_600_000
GROUP_BY_1_DAY = 86_400_000

# Device status values
STATUS_ONLINE = "online"
STATUS_REPLACED = "replaced"
