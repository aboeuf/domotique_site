import json
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

    return render_template("domotique_index.html", mesures=mesures)


@domotique_bp.route("/api/history/<device_id>")
@site_permission_required("domotique")
def domotique_history(device_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Query to retrieve history
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

    # MySQL datetime type is not serializable to JSON by default,
    # we convert timestamps to ISO format strings.
    for row in history:
        if row["timestamp"]:
            row["timestamp"] = row["timestamp"].isoformat()

    return jsonify(history)


@domotique_bp.route("/export/json")
@site_permission_required("domotique")
def domotique_export_json():
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Query to retrieve ALL history with associated room names
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

    # Convert datetime objects to ISO strings for JSON
    for row in history:
        if row["timestamp"]:
            row["timestamp"] = row["timestamp"].isoformat()

    # Serialize to clean JSON string (with indentation for readability)
    json_data = json.dumps(history, indent=4, ensure_ascii=False)

    # Return a response configured to trigger a file download
    return Response(
        json_data,
        mimetype="application/json",
        headers={
            "Content-Disposition": "attachment;filename=historique_domotique.json"
        },
    )
