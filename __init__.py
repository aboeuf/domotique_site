from flask import Blueprint, render_template
from app import get_db_connection
from utils import site_permission_required

domotique_bp = Blueprint('domotique', __name__, template_folder='domotique_templates', url_prefix="/domotique")

@domotique_bp.route('/')
@site_permission_required('domotique')
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
    
    return render_template('domotique_index.html', mesures=mesures)