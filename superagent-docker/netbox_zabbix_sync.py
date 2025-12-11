from flask import Flask, request, jsonify
import requests
import logging
import urllib3
import json
import os
from datetime import datetime

app = Flask(__name__)

# Zabbix API details - Use Bearer token authentication
ZABBIX_URL = os.environ.get('ZABBIX_URL', "http://zabbix-docker-zabbix-web-nginx-pgsql-1:8080/api_jsonrpc.php")
ZABBIX_API_TOKEN = os.environ.get('ZABBIX_API_TOKEN')
DEFAULT_HOSTGROUP_ID = os.environ.get('DEFAULT_HOSTGROUP_ID', "5")

# Default template IDs for each manufacturer
MANUFACTURER_TEMPLATES = {
    "Juniper": os.environ.get('DEFAULT_TEMPLATE_JUNIPER', "10231"),
    "Cisco": os.environ.get('DEFAULT_TEMPLATE_CISCO', "10218"),
    "Arista": os.environ.get('DEFAULT_TEMPLATE_ARISTA', "10254"),
    "Nokia": os.environ.get('DEFAULT_TEMPLATE_NOKIA', "10207"),
    "Alcatel-Lucent": os.environ.get('DEFAULT_TEMPLATE_ALCATEL_LUCENT', "10207"),
    "MikroTik": os.environ.get('DEFAULT_TEMPLATE_MIKROTIK', "10233"),
    "Dell": os.environ.get('DEFAULT_TEMPLATE_DELL', "10221"),
}

# Generic fallback templates
GENERIC_TEMPLATE_ID = os.environ.get('DEFAULT_TEMPLATE_GENERIC', "10226")
FALLBACK_TEMPLATE_ID = os.environ.get('FALLBACK_TEMPLATE_ID', "10563")

# Get listen port from environment or use default
LISTEN_PORT = int(os.environ.get('LISTEN_PORT', '5000'))

# Configure logging - Use environment variable for level
LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Create logger instance
logger = logging.getLogger(__name__)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def make_zabbix_request(method, params, timeout=10):
    """Make a request to Zabbix API with Bearer token authentication"""
    payload = {
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": 1
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ZABBIX_API_TOKEN}"
    }
    
    try:
        logger.debug(f"Making Zabbix request: {method}")
        response = requests.post(
            ZABBIX_URL,
            json=payload,
            headers=headers,
            timeout=timeout
        )
        
        if response.status_code == 200:
            result = response.json()
            if 'error' in result:
                logger.error(f"Zabbix API error for {method}: {result['error']}")
                return None
            return result
        else:
            logger.error(f"HTTP error {response.status_code} for {method}: {response.text}")
            return None
            
    except requests.exceptions.Timeout:
        logger.error(f"Timeout connecting to Zabbix API for {method}")
        return None
    except requests.exceptions.ConnectionError as e:
        logger.error(f"Connection error to Zabbix API for {method}: {e}")
        return None
    except Exception as e:
        logger.error(f"Error in Zabbix API request for {method}: {e}")
        return None

def fetch_zabbix_host_id(hostname):
    """Get host ID by hostname"""
    params = {
        "output": ["hostid"],
        "filter": {"host": hostname}
    }
    result = make_zabbix_request("host.get", params)
    if result and 'result' in result and len(result['result']) > 0:
        return result['result'][0]['hostid']
    return None

def create_host_in_zabbix(hostname, ip, templateid, groupid):
    """Create a new host in Zabbix"""
    params = {
        "host": hostname,
        "interfaces": [{
            "type": 2,  # SNMP interface
            "main": 1,
            "useip": 1,
            "ip": ip,
            "dns": "",
            "port": "161",
            "details": {
                "version": 2,
                "bulk": 1,
                "community": "{$SNMP_COMMUNITY}"  # Use macro or hardcode "public"
            }
        }],
        "groups": groupid,
        "templates": [{"templateid": templateid}]
    }
    return make_zabbix_request("host.create", params)

def update_host_in_zabbix(hostid, hostname):
    """Update host name in Zabbix"""
    params = {
        "hostid": hostid,
        "host": hostname,
        "name": hostname
    }
    return make_zabbix_request("host.update", params)

def get_host_group_id_by_groupname(groupname):
    """Get host group ID by group name"""
    params = {
        "output": ["groupid"],
        "filter": {"name": [groupname]}
    }
    result = make_zabbix_request("hostgroup.get", params)
    if result and 'result' in result and len(result['result']) > 0:
        return result['result'][0]['groupid']
    return None

def create_hostgroup_in_zabbix(sitename):
    """Create a new host group in Zabbix"""
    params = {
        "name": sitename
    }
    result = make_zabbix_request("hostgroup.create", params)
    if result and 'result' in result and 'groupids' in result['result']:
        return result['result']['groupids'][0]
    return None

def get_host_template_id_by_templatename(templatename):
    """Get template ID by template name"""
    params = {
        "output": ["templateid"],
        "filter": {"host": templatename}
    }
    result = make_zabbix_request("template.get", params)
    if result and 'result' in result and len(result['result']) > 0:
        return result['result'][0]['templateid']
    return None

def determine_device_type(device_role):
    """
    Determine if device is a Router or Switch based on role name.
    Returns 'Router', 'Switch', or None
    """
    if not device_role:
        return None
    
    role_lower = device_role.lower()
    
    # Check for Router keywords
    if 'router' in role_lower:
        return 'Router'
    
    # Check for Switch keywords
    if 'switch' in role_lower:
        return 'Switch'
    
    return None

def determine_cisco_platform(platform_name):
    """
    Determine Cisco platform type from platform name.
    Returns platform identifier like 'IOSXR', 'NXOS', 'IOS', 'Nexus', etc.
    """
    if not platform_name:
        return None
    
    platform_lower = platform_name.lower()
    
    # Check for specific Cisco platforms
    if 'iosxr' in platform_lower or 'ios-xr' in platform_lower or 'xr' in platform_lower:
        return 'IOSXR'
    elif 'nxos' in platform_lower or 'nx-os' in platform_lower:
        return 'NXOS'
    elif 'nexus' in platform_lower:
        return 'Nexus'
    elif 'ios' in platform_lower:
        return 'IOS'
    elif 'asa' in platform_lower:
        return 'ASA'
    
    return None

def get_template_id_for_device(manufacturer, device_role, platform_name=None):
    """
    Get the appropriate template ID for a device based on manufacturer, role, and platform.
    
    For Cisco devices:
    1. Try {Manufacturer} {Platform} Router/Switch (e.g., "Cisco IOSXR Router", "Cisco NXOS Switch")
    2. Try {Manufacturer} {Platform} by SNMP (e.g., "Cisco IOS by SNMP", "Cisco Nexus by SNMP")
    3. Try {Manufacturer} by SNMP (manufacturer default)
    4. Try Generic template (10226) if manufacturer is known
    5. Use Network Generic Device by SNMP (10563) as final fallback
    
    For other manufacturers:
    1. Try {Manufacturer} Router/Switch based on role
    2. Try {Manufacturer} by SNMP (manufacturer default)
    3. Try Generic template (10226) if manufacturer is known
    4. Use Network Generic Device by SNMP (10563) as final fallback
    
    Args:
        manufacturer: Device manufacturer name (e.g., "Juniper", "Cisco")
        device_role: Device role name (e.g., "Access Router", "Core Switch")
        platform_name: Platform name (used for Cisco devices)
    
    Returns:
        tuple: (template_id, template_name) - The ID and name of the selected template
    """
    
    # Normalize manufacturer name for case-insensitive matching
    manufacturer_normalized = None
    for known_mfg in MANUFACTURER_TEMPLATES.keys():
        if known_mfg.lower() == manufacturer.lower():
            manufacturer_normalized = known_mfg
            break
    
    # Determine device type from role
    device_type = determine_device_type(device_role)
    
    logger.info(f"Template selection: Manufacturer='{manufacturer}' (normalized='{manufacturer_normalized}'), Role='{device_role}', Type='{device_type}', Platform='{platform_name}'")
    
    # Special handling for Cisco devices
    if manufacturer_normalized and manufacturer_normalized.lower() == 'cisco':
        cisco_platform = determine_cisco_platform(platform_name)
        logger.debug(f"Cisco device detected, platform: {cisco_platform}")
        
        # 1. Try Cisco platform + role specific template: Cisco IOSXR Router, Cisco NXOS Switch
        if cisco_platform and device_type:
            template_name = f"Cisco {cisco_platform} {device_type}"
            logger.debug(f"Trying Cisco platform+role template: {template_name}")
            template_id = get_host_template_id_by_templatename(template_name)
            if template_id:
                logger.info(f"✓ Using Cisco platform+role template: {template_name} (ID: {template_id})")
                return template_id, template_name
        
        # 2. Try Cisco platform default: Cisco IOS by SNMP, Cisco Nexus by SNMP
        if cisco_platform:
            template_name = f"Cisco {cisco_platform} by SNMP"
            logger.debug(f"Trying Cisco platform template: {template_name}")
            template_id = get_host_template_id_by_templatename(template_name)
            if template_id:
                logger.info(f"✓ Using Cisco platform template: {template_name} (ID: {template_id})")
                return template_id, template_name
    
    # Standard flow for all manufacturers (including Cisco if platform-specific not found)
    # 1. Try role-specific template: {Manufacturer} Router/Switch
    if manufacturer_normalized and device_type:
        template_name = f"{manufacturer_normalized} {device_type}"
        logger.debug(f"Trying role-specific template: {template_name}")
        template_id = get_host_template_id_by_templatename(template_name)
        if template_id:
            logger.info(f"✓ Using role-specific template: {template_name} (ID: {template_id})")
            return template_id, template_name
    
    # 2. Try manufacturer default: {Manufacturer} by SNMP
    if manufacturer_normalized:
        template_name = f"{manufacturer_normalized} by SNMP"
        logger.debug(f"Trying manufacturer default template: {template_name}")
        template_id = get_host_template_id_by_templatename(template_name)
        if template_id:
            logger.info(f"✓ Using manufacturer default template: {template_name} (ID: {template_id})")
            return template_id, template_name
        
        # 2b. Also try using the pre-configured default template ID for this manufacturer
        if manufacturer_normalized in MANUFACTURER_TEMPLATES:
            default_id = MANUFACTURER_TEMPLATES[manufacturer_normalized]
            logger.info(f"✓ Using configured default template ID for {manufacturer_normalized}: {default_id}")
            return default_id, f"{manufacturer_normalized} (default ID)"
    
    # 3. Try generic template if manufacturer was recognized
    if manufacturer_normalized and GENERIC_TEMPLATE_ID:
        logger.info(f"✓ Using generic template for known manufacturer (ID: {GENERIC_TEMPLATE_ID})")
        return GENERIC_TEMPLATE_ID, "Generic"
    
    # 4. Final fallback: Network Generic Device by SNMP (for unknown manufacturers)
    logger.warning(f"No specific template found for unknown manufacturer '{manufacturer}', using final fallback (ID: {FALLBACK_TEMPLATE_ID})")
    return FALLBACK_TEMPLATE_ID, "Network Generic Device by SNMP"

def delete_host_in_zabbix(hostid):
    """Delete host from Zabbix"""
    params = [hostid]
    return make_zabbix_request("host.delete", params)

def get_interface_id_by_host_id(hostid):
    """Get interface ID for a host"""
    params = {
        "output": ["interfaceid"],
        "hostids": hostid
    }
    result = make_zabbix_request("hostinterface.get", params)
    if result and 'result' in result and len(result['result']) > 0:
        return result['result'][0]['interfaceid']
    return None

def update_host_interface_in_zabbix(interfaceid, ip):
    """Update host interface IP"""
    params = {
        "interfaceid": interfaceid,
        "ip": ip
    }
    return make_zabbix_request("hostinterface.update", params)

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.json
        if not data:
            logger.error("No JSON data received")
            return jsonify({"status": "error", "message": "No data received"}), 400
        
        logger.info(f"Received webhook from NetBox")
        
        # Extract data
        model = data.get("model")
        event = data.get("event")
        device_data = data.get("data", {})
        
        if model != "device":
            logger.info(f"Ignoring non-device event: {model}")
            return jsonify({"status": "ignored", "reason": "Not a device event"}), 400
        
        device_name = device_data.get("name")
        device_status = device_data.get("status", {}).get("value", "").lower()
        device_site = device_data.get("site", {}).get("name", "")
        device_role = device_data.get("role", {}).get("name", "")
        device_manufacture = device_data.get("device_type", {}).get("manufacturer", {}).get("name", "")
        device_platform = device_data.get("platform", {}).get("name", "") if device_data.get("platform") else None
        
        primary_ip = (
            device_data.get("primary_ip4", {}).get("address", "").split('/')[0]
            if device_data.get("primary_ip4") else None
        )
        
        logger.info(f"Event: {event}, Device: {device_name}, Manufacturer: {device_manufacture}, Role: {device_role}, Platform: {device_platform}, IP: {primary_ip}, Status: {device_status}")
        
        # Process based on event type
        if event == "created":
            return handle_device_created(device_name, primary_ip, device_status, device_site, device_role, device_manufacture, device_platform)
        elif event == "updated":
            return handle_device_updated(data, device_name, primary_ip, device_status, device_site, device_role, device_manufacture, device_platform)
        elif event == "deleted":
            return handle_device_deleted(device_name)
        else:
            logger.warning(f"Unhandled event type: {event}")
            return jsonify({"status": "ignored", "reason": f"Unhandled event: {event}"}), 400
            
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)}), 500

def handle_device_created(device_name, primary_ip, device_status, device_site, device_role, device_manufacture, device_platform=None):
    """Handle device creation event"""
    if device_status != "active":
        logger.info(f"Skipping inactive device: {device_name}")
        return jsonify({"status": "ignored", "reason": "Device not active"}), 200
    
    # Use default IP if none provided
    if not primary_ip:
        primary_ip = "10.11.12.13"
        logger.warning(f"No primary IP for {device_name}, using default: {primary_ip}")
    
    # Get appropriate template using the new logic
    template_id, template_name = get_template_id_for_device(device_manufacture, device_role, device_platform)
    
    logger.info(f"Selected template for {device_name}: {template_name} (ID: {template_id})")
    
    # Get or create groups
    group_ids = []
    for group_name in [device_role, device_site]:
        if group_name:
            group_id = get_host_group_id_by_groupname(group_name)
            if not group_id:
                group_id = create_hostgroup_in_zabbix(group_name)
                if group_id:
                    logger.info(f"Created new host group: {group_name} (ID: {group_id})")
            if group_id:
                group_ids.append({"groupid": group_id})
    
    if not group_ids:
        logger.warning(f"No groups created/retrieved for {device_name}")
        group_ids = [{"groupid": DEFAULT_HOSTGROUP_ID}]
    
    # Create host in Zabbix
    logger.info(f"Creating host {device_name} in Zabbix with IP {primary_ip}, template {template_name}")
    response = create_host_in_zabbix(device_name, primary_ip, template_id, group_ids)
    
    if response and 'result' in response and 'hostids' in response['result']:
        host_id = response['result']['hostids'][0]
        logger.info(f"✓ Successfully created host {device_name} with ID: {host_id}")
        return jsonify({
            "status": "success",
            "action": "created",
            "host_id": host_id,
            "template": template_name,
            "message": f"Host {device_name} created successfully with template {template_name}"
        }), 200
    else:
        logger.error(f"✗ Failed to create host {device_name}: {response}")
        return jsonify({
            "status": "error",
            "message": f"Failed to create host in Zabbix",
            "details": str(response) if response else "No response from Zabbix"
        }), 500

def handle_device_updated(data, device_name, primary_ip, device_status, device_site, device_role, device_manufacture, device_platform=None):
    """Handle device update event"""
    if device_status != "active":
        logger.info(f"Skipping update for inactive device: {device_name}")
        return jsonify({"status": "ignored", "reason": "Device not active"}), 200
    
    # Get old name from snapshots
    prechange = data.get("snapshots", {}).get("prechange", {}).get("name", device_name)
    logger.info(f"Updating device: old={prechange}, new={device_name}")
    
    # Find host by old name
    host_id = fetch_zabbix_host_id(prechange)
    if not host_id:
        logger.warning(f"Host not found in Zabbix: {prechange}")
        # Try with new name
        host_id = fetch_zabbix_host_id(device_name)
        if not host_id:
            logger.error(f"Host not found with either old or new name: {prechange} / {device_name}")
            return jsonify({
                "status": "error",
                "message": f"Host {device_name} not found in Zabbix"
            }), 404
    
    # Update host name if changed
    if prechange != device_name:
        logger.info(f"Renaming host {prechange} to {device_name}")
        update_result = update_host_in_zabbix(host_id, device_name)
        if not update_result:
            logger.warning(f"Failed to update host name for {host_id}")
    
    # Update IP if provided
    if primary_ip:
        interface_id = get_interface_id_by_host_id(host_id)
        if interface_id:
            logger.info(f"Updating IP for {device_name} to {primary_ip}")
            update_host_interface_in_zabbix(interface_id, primary_ip)
        else:
            logger.warning(f"No interface found for host {host_id}")
    
    logger.info(f"✓ Successfully updated host {device_name} (ID: {host_id})")
    return jsonify({
        "status": "success",
        "action": "updated",
        "host_id": host_id,
        "message": f"Host {device_name} updated successfully"
    }), 200

def handle_device_deleted(device_name):
    """Handle device deletion event"""
    host_id = fetch_zabbix_host_id(device_name)
    if not host_id:
        logger.warning(f"Host not found for deletion: {device_name}")
        return jsonify({
            "status": "ignored",
            "reason": f"Host {device_name} not found in Zabbix"
        }), 200
    
    logger.info(f"Deleting host {device_name} (ID: {host_id})")
    delete_result = delete_host_in_zabbix(host_id)
    
    if delete_result and 'result' in delete_result and 'hostids' in delete_result['result']:
        logger.info(f"✓ Successfully deleted host {device_name}")
        return jsonify({
            "status": "success",
            "action": "deleted",
            "host_id": host_id,
            "message": f"Host {device_name} deleted successfully"
        }), 200
    else:
        logger.error(f"✗ Failed to delete host {device_name}: {delete_result}")
        return jsonify({
            "status": "error",
            "message": f"Failed to delete host from Zabbix",
            "details": str(delete_result) if delete_result else "No response from Zabbix"
        }), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "healthy",
        "service": "netbox-zabbix-sync",
        "port": LISTEN_PORT,
        "zabbix_url": ZABBIX_URL,
        "timestamp": datetime.now().isoformat()
    }), 200

@app.route('/test-zabbix', methods=['GET'])
def test_zabbix():
    """Test Zabbix API connection"""
    params = {}
    result = make_zabbix_request("apiinfo.version", params)
    if result and 'result' in result:
        return jsonify({
            "status": "connected",
            "zabbix_version": result['result'],
            "zabbix_url": ZABBIX_URL
        }), 200
    else:
        return jsonify({
            "status": "disconnected",
            "zabbix_url": ZABBIX_URL,
            "error": "Cannot connect to Zabbix API"
        }), 500

@app.route('/', methods=['GET'])
def index():
    """Root endpoint"""
    return jsonify({
        "service": "NetBox-Zabbix Sync Agent",
        "version": "2.0.0",
        "supported_manufacturers": list(MANUFACTURER_TEMPLATES.keys()),
        "endpoints": {
            "webhook": "/webhook (POST)",
            "health": "/health (GET)",
            "test_zabbix": "/test-zabbix (GET)"
        }
    }), 200

if __name__ == "__main__":
    # Test Zabbix connection on startup
    logger.info(f"Starting NetBox-Zabbix Sync Agent v2.0.0 on port {LISTEN_PORT}")
    logger.info(f"Zabbix URL: {ZABBIX_URL}")
    logger.info(f"Supported manufacturers: {', '.join(MANUFACTURER_TEMPLATES.keys())}")
    
    # Test connection
    test_result = make_zabbix_request("apiinfo.version", {})
    if test_result and 'result' in test_result:
        logger.info(f"✓ Connected to Zabbix API v{test_result['result']}")
    else:
        logger.error("✗ Cannot connect to Zabbix API")
    
    # Start Flask app (for development only)
    # In production, use gunicorn as specified in Dockerfile
    logger.info(f"Development server starting on port {LISTEN_PORT}")
    app.run(host="0.0.0.0", port=LISTEN_PORT, debug=True)
