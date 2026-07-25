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


@domotique_bp.route("/appareils")
@site_permission_required("domotique")
def domotique_appareils():
    return render_template("section_placeholder.html", section_name="Appareils")


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
