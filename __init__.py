from flask import Blueprint, render_template, jsonify
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
            SELECT t1.*, s.friendly_name AS piece,
                   ROW_NUMBER() OVER (PARTITION BY t1.sensor_id ORDER BY t1.timestamp DESC) as rn
            FROM thermometer_data t1
            INNER JOIN sensors s ON t1.sensor_id = s.ieee_address
        )
        SELECT * FROM ranked_measurements WHERE rn = 1
        ORDER BY piece ASC
    """
    cursor.execute(query)
    mesures = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template("domotique_index.html", mesures=mesures)


@domotique_bp.route("/api/history/<sensor_id>")
@site_permission_required("domotique")
def domotique_history(sensor_id):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    # Requête pour récupérer l'historique des dernières 24 heures
    query = """
        SELECT timestamp, temperature, humidity 
        FROM thermometer_data 
        WHERE sensor_id = %s 
        AND timestamp >= NOW() - INTERVAL 24 HOUR
        ORDER BY timestamp ASC
    """
    cursor.execute(query, (sensor_id,))
    history = cursor.fetchall()

    cursor.close()
    conn.close()

    # Le type datetime de MySQL n'est pas sérialisable en JSON par défaut,
    # on convertit les timestamps en chaînes de caractères de type ISO.
    for row in history:
        if row["timestamp"]:
            row["timestamp"] = row["timestamp"].isoformat()

    return jsonify(history)
