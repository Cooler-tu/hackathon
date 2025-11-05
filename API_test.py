import requests
import time
import hmac
import hashlib

BASE_URL = "https://mock-api.roostoo.com"
API_KEY = "USEAPIKEYASMYID"
SECRET_KEY = "S1XP1e3UZj6A7H5fATj0jNhqPxxdSJYdInClVN65XAbvqqMKjVHjA7PZj4W12oep"
# --------------------------------------------------------

def _get_timestamp():
    """Returns a 13-digit millisecond timestamp as a string."""
    return str(int(time.time() * 1000))

def _get_signed_headers(payload={}):
    """
    Creates a signature for a given payload (dict) and returns
    the correct headers for a SIGNED (RCL_TopLevelCheck) request.
    """
    # 1. Add timestamp to the payload
    payload['timestamp'] = _get_timestamp()
    
    # 2. Sort keys and create the totalParams string
    sorted_keys = sorted(payload.keys())
    total_params = "&".join(f"{key}={payload[key]}" for key in sorted_keys)
    
    # 3. Create HMAC-SHA256 signature
    signature = hmac.new(
        SECRET_KEY.encode('utf-8'),
        total_params.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    # 4. Create headers
    headers = {
        'RST-API-KEY': API_KEY,
        'MSG-SIGNATURE': signature
    }
    
    return headers, payload, total_params

# --- Now we can define functions for each API call ---
