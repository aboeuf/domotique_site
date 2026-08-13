import json
import os
import threading
import time
from flask import Blueprint, render_template, jsonify, Response, request
from app import get_db_connection
from utils import site_permission_required

domotique_bp = Blueprint(
    "domotique",
    __name__,
    template_folder="domotique_templates",
    url_prefix="/domotique",
)

MQTT_BROKER_HOST = os.environ.get("MQTT_BROKER_HOST", "localhost")
MQTT_BROKER_PORT = int(os.environ.get("MQTT_BROKER_PORT", 1883))

# In-memory cache for device states updated via MQTT
MQTT_DEVICE_STATES = {}
_mqtt_thread_started = False
_mqtt_thread_lock = threading.Lock()


def load_initial_states():
    """Initializes MQTT_DEVICE_STATES cache using the Z2M state file on disk if available."""
    state_path = os.environ.get("Z2M_STATE_PATH")
    if not state_path or not os.path.exists(state_path):
        return

    try:
        with open(state_path, "r", encoding="utf-8") as f:
            initial_data = json.load(f)
            if isinstance(initial_data, dict):
                MQTT_DEVICE_STATES.update(initial_data)
    except Exception as e:
        print(f"Erreur lors du chargement des états initiaux depuis {state_path}: {e}")


# Pre-fill states cache from Z2M_STATE_PATH file
load_initial_states()


def _on_mqtt_message(client, userdata, msg):
    """Callback triggered when an MQTT message is received."""
    try:
        topic = msg.topic
        # Zigbee2MQTT topics are structured as zigbee2mqtt/<device_id_or_friendly_name>
        parts = topic.split("/")
        if len(parts) == 2 and parts[0] == "zigbee2mqtt":
            device_key = parts[1]
            if device_key in ["bridge", "logging"]:
                return
            payload_str = msg.payload.decode("utf-8")
            if not payload_str:
                return
            try:
                data = json.loads(payload_str)
                if isinstance(data, dict):
                    if device_key not in MQTT_DEVICE_STATES:
                        MQTT_DEVICE_STATES[device_key] = {}
                    MQTT_DEVICE_STATES[device_key].update(data)

                    # Also update by IEEE address if device_key is a friendly name
                    friendly_map = get_friendly_names_mapping()
                    for ieee, fname in friendly_map.items():
                        if fname == device_key:
                            if ieee not in MQTT_DEVICE_STATES:
                                MQTT_DEVICE_STATES[ieee] = {}
                            MQTT_DEVICE_STATES[ieee].update(data)
                            break
            except json.JSONDecodeError:
                pass
    except Exception as e:
        print(f"Erreur lors du traitement du message MQTT: {e}")


def _bg_shutter_poller():
    """Background loop that polls physical roller shutter states via MQTT every second."""
    while True:
        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)
            query = "SELECT ieee_address FROM devices WHERE role_id = 4"
            cursor.execute(query)
            shutter_devices = cursor.fetchall()
            cursor.close()
            conn.close()

            friendly_map = get_friendly_names_mapping()

            for dev in shutter_devices:
                ieee = dev["ieee_address"]
                friendly_name = friendly_map.get(ieee)
                target = friendly_name if friendly_name else ieee
                request_device_state(target)
                if friendly_name:
                    request_device_state(ieee)
        except Exception as e:
            print(f"Erreur dans le bouclage de poll des volets: {e}")
        time.sleep(1)


def start_mqtt_state_listener():
    """Starts a background thread listening for Zigbee2MQTT state updates and polling shutters."""
    global _mqtt_thread_started
    with _mqtt_thread_lock:
        if _mqtt_thread_started:
            return
        _mqtt_thread_started = True

    def run_listener():
        import paho.mqtt.client as mqtt
        try:
            try:
                client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
            except AttributeError:
                client = mqtt.Client()

            client.on_message = _on_mqtt_message
            client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
            client.subscribe("zigbee2mqtt/#")

            # Start background shutter state polling thread
            poller_thread = threading.Thread(target=_bg_shutter_poller, daemon=True)
            poller_thread.start()

            client.loop_forever()
        except Exception as e:
            print(f"Erreur dans le thread de souscription MQTT: {e}")

    thread = threading.Thread(target=run_listener, daemon=True)
    thread.start()


# Start background MQTT state listener
start_mqtt_state_listener()


def publish_mqtt_message(topic, payload):
    """Publie un message MQTT vers le broker."""
    try:
        import paho.mqtt.client as mqtt
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        except AttributeError:
            client = mqtt.Client()
        client.connect(MQTT_BROKER_HOST, MQTT_BROKER_PORT, 60)
        client.publish(topic, json.dumps(payload))
        client.disconnect()
        return True
    except Exception as e:
        print(f"Erreur d'envoi MQTT vers {topic}: {e}")
        return False


def request_device_state(device_key):
    """Actively requests state and position update from Zigbee2MQTT for a given device key (IEEE address or friendly name)."""
    topic = f"zigbee2mqtt/{device_key}/get"
    payload = {"state": "", "position": ""}
    publish_mqtt_message(topic, payload)


def get_friendly_names_mapping():
    """Régénère le mapping (IEEE address -> friendly_name) depuis configuration.yaml."""
    state_path = os.environ.get("Z2M_STATE_PATH")
    if not state_path:
        return {}

    config_path = os.path.join(os.path.dirname(state_path), "configuration.yaml")
    mapping = {}

    if not os.path.exists(config_path):
        return mapping

    # 1. Tentative d'ouverture et de parsing via PyYAML
    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            config_data = yaml.safe_load(f) or {}
            devices = config_data.get("devices", {})
            for ieee, dev_info in devices.items():
                if isinstance(dev_info, dict) and "friendly_name" in dev_info:
                    mapping[ieee] = dev_info["friendly_name"]
        return mapping
    except ImportError:
        # 2. Parseur de secours sans dépendance externe si PyYAML n'est pas installé
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                current_ieee = None
                for line in f:
                    line_str = line.strip()
                    # Détection d'une clé d'adresse IEEE (ex: '0x001788010f138966':)
                    if line_str.startswith(("'0x", '"0x', "0x")) and ":" in line_str:
                        current_ieee = line_str.split(":")[0].strip("'\" ")
                    elif current_ieee and "friendly_name:" in line_str:
                        fname = line_str.split("friendly_name:", 1)[1].strip().strip("'\"")
                        mapping[current_ieee] = fname
                        current_ieee = None
            return mapping
        except Exception as e:
            print(f"Erreur lors de la lecture (fallback) de {config_path}: {e}")
    except Exception as e:
        print(f"Erreur lors de la lecture de {config_path}: {e}")

    return mapping


@domotique_bp.route("/")
@site_permission_required("domotique")
def domotique_index():
    return render_template("domotique_index.html")


@domotique_bp.route("/thermometres")
@site_permission_required("domotique")
def domotique_thermometres():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        WITH ranked_measurements AS (
            SELECT t1.*, COALESCE(r.name, d.name) AS piece,
                   ROW_NUMBER() OVER (PARTITION BY t1.device_id ORDER BY t1.timestamp DESC) as rn
            FROM thermometer_data t1
            INNER JOIN devices d ON t1.device_id = d.ieee_address
            LEFT JOIN rooms r ON d.room_id = r.id
            WHERE d.role_id = 1
        )
        SELECT * FROM ranked_measurements WHERE rn = 1
        ORDER BY piece ASC
    """
    cursor.execute(query)
    mesures = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("domotique_thermometers.html", mesures=mesures)


@domotique_bp.route("/pieces")
@site_permission_required("domotique")
def domotique_pieces():
    return render_template("section_placeholder.html", section_name="Pièces")


@domotique_bp.route("/eclairage")
@site_permission_required("domotique")
def domotique_eclairage():
    return render_template("section_placeholder.html", section_name="Éclairage")


@domotique_bp.route("/volets")
@site_permission_required("domotique")
def domotique_volets():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        SELECT d.ieee_address, d.name AS device_name, r.name AS room_name
        FROM devices d
        LEFT JOIN rooms r ON d.room_id = r.id
        WHERE d.role_id = 4
        ORDER BY r.name ASC, d.name ASC
    """
    cursor.execute(query)
    db_volets = cursor.fetchall()
    cursor.close()
    conn.close()

    states = MQTT_DEVICE_STATES
    friendly_map = get_friendly_names_mapping()

    volets = []
    for row in db_volets:
        ieee = row["ieee_address"]
        friendly_name = friendly_map.get(ieee)

        # Query state on page render
        request_device_state(friendly_name or ieee)

        st = states.get(ieee) or (states.get(friendly_name) if friendly_name else {}) or {}

        room_name = row["room_name"]
        device_name = row["device_name"]

        if room_name and device_name:
            piece = f"{room_name} - {device_name}"
        else:
            piece = room_name or device_name or ieee

        volets.append({
            "ieee_address": ieee,
            "friendly_name": friendly_name or "",
            "piece": piece,
            "device_name": device_name,
            "state": st.get("state", "UNKNOWN"),
            "position": st.get("position", 0),
            "motor_run_status": st.get("motor_run_status", "idle"),
            "linkquality": st.get("linkquality")
        })

    return render_template("domotique_volets.html", volets=volets)


@domotique_bp.route("/api/volets/control", methods=["POST"])
@site_permission_required("domotique")
def api_volets_control():
    data = request.get_json() or {}
    ieee_address = data.get("ieee_address")
    action = data.get("action")  # 'OPEN', 'CLOSE', 'STOP', or None if position is given
    position = data.get("position")  # integer 0-100 or None

    if not ieee_address:
        return jsonify({"success": False, "error": "Adresse IEEE manquante"}), 400

    friendly_map = get_friendly_names_mapping()
    target = friendly_map.get(ieee_address, ieee_address)

    topic = f"zigbee2mqtt/{target}/set"
    payload = {}

    if action in ["OPEN", "CLOSE", "STOP"]:
        payload["state"] = action
    elif position is not None:
        try:
            payload["position"] = int(position)
        except ValueError:
            return jsonify({"success": False, "error": "Position invalide"}), 400
    else:
        return jsonify({"success": False, "error": "Action ou position requise"}), 400

    success = publish_mqtt_message(topic, payload)
    if success:
        request_device_state(target)
        return jsonify({"success": True, "topic": topic, "payload": payload})
    else:
        return jsonify({"success": False, "error": "Échec de l'envoi MQTT"}), 500


@domotique_bp.route("/devices")
@site_permission_required("domotique")
def domotique_devices():
    devices = MQTT_DEVICE_STATES

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        SELECT d.ieee_address, d.name AS device_name, r.name AS room_name, dr.name AS role_name, d.role_id
        FROM devices d
        LEFT JOIN rooms r ON d.room_id = r.id
        LEFT JOIN device_roles dr ON d.role_id = dr.id
    """
    cursor.execute(query)
    db_devices = cursor.fetchall()
    cursor.close()
    conn.close()

    db_lookup = {row["ieee_address"]: row for row in db_devices}
    friendly_map = get_friendly_names_mapping()
    reverse_friendly_map = {fname: ieee for ieee, fname in friendly_map.items()}

    # Merge database fields and MQTT friendly name into each device
    for key, state in devices.items():
        ieee = key if key in db_lookup else reverse_friendly_map.get(key, key)
        db_info = db_lookup.get(ieee, {})

        state["ieee_display"] = ieee
        state["device_name"] = db_info.get("device_name")
        state["room_name"] = db_info.get("room_name")
        state["role_name"] = db_info.get("role_name")
        state["role_id"] = db_info.get("role_id")
        state["friendly_name"] = friendly_map.get(ieee, "")

    return render_template("domotique_devices.html", devices=devices)


@domotique_bp.route("/api/devices/state")
@site_permission_required("domotique")
def api_devices_state():
    return jsonify(MQTT_DEVICE_STATES)


@domotique_bp.route("/api/history/<device_id>")
@site_permission_required("domotique")
def domotique_history(device_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        SELECT timestamp, temperature, humidity 
        FROM thermometer_data 
        WHERE device_id = %s
        ORDER BY timestamp ASC
    """
    cursor.execute(query, (device_id,))
    history = cursor.fetchall()

    cursor.close()
    conn.close()

    for row in history:
        if row["timestamp"]:
            row["timestamp"] = row["timestamp"].isoformat()

    return jsonify(history)


@domotique_bp.route("/export/json")
@site_permission_required("domotique")
def domotique_export_json():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        SELECT t.timestamp, t.device_id, COALESCE(r.name, d.name) AS piece, 
               t.temperature, t.humidity, t.battery, t.linkquality
        FROM thermometer_data t
        INNER JOIN devices d ON t.device_id = d.ieee_address
        LEFT JOIN rooms r ON d.room_id = r.id
        WHERE d.role_id = 1
        ORDER BY t.timestamp DESC
    """
    cursor.execute(query)
    history = cursor.fetchall()

    cursor.close()
    conn.close()

    for row in history:
        if row["timestamp"]:
            row["timestamp"] = row["timestamp"].isoformat()

    json_data = json.dumps(history, indent=4, ensure_ascii=False)

    return Response(
        json_data,
        mimetype="application/json",
        headers={
            "Content-Disposition": "attachment;filename=historique_domotique.json"
        },
    )
