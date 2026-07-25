import json
import os
from flask import Blueprint, render_template, jsonify, Response
from app import get_db_connection
from utils import site_permission_required

domotique_bp = Blueprint(
    "domotique",
    __name__,
    template_folder="domotique_templates",
    url_prefix="/domotique",
)


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
    return render_template("section_placeholder.html", section_name="Volets")


@domotique_bp.route("/devices")
@site_permission_required("domotique")
def domotique_devices():
    state_path = os.environ.get("Z2M_STATE_PATH")
    devices = {}
    if state_path and os.path.exists(state_path):
        with open(state_path, "r") as f:
            devices = json.load(f)

    # Fetch database info for each device (device name, room name, role name)
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    query = """
        SELECT d.ieee_address, d.name AS device_name, r.name AS room_name, dr.name AS role_name
        FROM devices d
        LEFT JOIN rooms r ON d.room_id = r.id
        LEFT JOIN device_roles dr ON d.role_id = dr.id
    """
    cursor.execute(query)
    db_devices = cursor.fetchall()
    cursor.close()
    conn.close()

    # Build a lookup keyed by ieee_address
    db_lookup = {row["ieee_address"]: row for row in db_devices}

    # Merge database fields into each device
    for ieee, state in devices.items():
        db_info = db_lookup.get(ieee, {})
        state["device_name"] = db_info.get("device_name")
        state["room_name"] = db_info.get("room_name")
        state["role_name"] = db_info.get("role_name")

    return render_template("domotique_devices.html", devices=devices)


@domotique_bp.route("/api/devices/state")
@site_permission_required("domotique")
def api_devices_state():
    state_path = os.environ.get("Z2M_STATE_PATH")
    if not state_path or not os.path.exists(state_path):
        return jsonify({"error": "State file not found"}), 404
    with open(state_path, "r") as f:
        data = json.load(f)
    return jsonify(data)


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
