import paho.mqtt.client as mqtt
import json
import sqlite3
import datetime
import logging
import os
import requests
from flask import Flask, render_template, jsonify

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# MQTT Configuration
MQTT_BROKER = "localhost"
MQTT_PORT = 1883
CLIENT_ID = "reservoir_processor"

# Database setup
DB_NAME = "reservoir_data.db"

# CDEC API endpoint - for direct fetching if MQTT fails
CDEC_BASE_URL = "https://cdec.water.ca.gov/dynamicapp/req/JSONDataServlet"

# Reservoir CDEC IDs
RESERVOIRS = {
    "SHA": "Shasta Lake",
    "ORO": "Lake Oroville",
    "CLE": "Trinity Lake",
    "NML": "New Melones Lake",
    "SNL": "San Luis Reservoir",
    "DNP": "Don Pedro Reservoir", 
    "BER": "Lake Berryessa",
    "FOL": "Folsom Lake",
    "BUL": "New Bullards Bar Reservoir",
    "PNF": "Pine Flat Lake"
}

# Create Flask app for web dashboard
app = Flask(__name__)

# Setup database
def setup_database():
    """Create SQLite database and tables if they don't exist"""
    if not os.path.exists(DB_NAME):
        logger.info(f"Creating new database: {DB_NAME}")
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Create table for reservoir data
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS reservoir_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reservoir_id TEXT NOT NULL,
        reservoir_name TEXT NOT NULL,
        storage_value REAL,
        storage_unit TEXT,
        percent_capacity REAL,
        percent_average REAL,
        measurement_time TEXT,
        received_time TEXT
    )
    ''')
    
    # Create table for historical trends
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS reservoir_trends (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        reservoir_id TEXT NOT NULL,
        date TEXT NOT NULL,
        storage_value REAL,
        percent_capacity REAL
    )
    ''')
    
    conn.commit()
    conn.close()
    logger.info("Database setup complete")

# Directly fetch data from CDEC API
def fetch_reservoir_data(station_id):
    """Fetch data directly from CDEC API if MQTT isn't providing data"""
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    params = {
        "Stations": station_id,
        "SensorNums": "15",  # 15 is storage, 6 is elevation
        "dur_code": "D",
        "Start": today,
        "End": today
    }
    
    try:
        logger.info(f"Fetching data for {station_id} directly from CDEC")
        response = requests.get(CDEC_BASE_URL, params=params)
        if response.status_code == 200:
            data = response.json()
            logger.info(f"Received data from CDEC for {station_id}: {json.dumps(data)[:200]}...")
            return data
        logger.error(f"Failed to get data from CDEC for {station_id}: {response.status_code}")
        return None
    except Exception as e:
        logger.error(f"Error fetching data from CDEC for {station_id}: {e}")
        return None

# Get reservoir capacity and historical averages
def get_reservoir_info(station_id):
    """Get capacity and historical average info - would normally come from a database"""
    # This is a simplified approach - in a real system, you'd get this from a database
    # or another API. These are approximate values for demonstration.
    capacity_info = {
        "SHA": {"capacity": 4552, "current_percent": 87, "historical_percent": 111},
        "ORO": {"capacity": 3537, "current_percent": 87, "historical_percent": 120},
        "CLE": {"capacity": 2448, "current_percent": 85, "historical_percent": 117},
        "FOL": {"capacity": 977, "current_percent": 79, "historical_percent": 128},
        "BUL": {"capacity": 966, "current_percent": 96, "historical_percent": 113},
        "NML": {"capacity": 2400, "current_percent": 82, "historical_percent": 133},
        "SNL": {"capacity": 2041, "current_percent": 65, "historical_percent": 98},
        "DNP": {"capacity": 2030, "current_percent": 82, "historical_percent": 109},
        "BER": {"capacity": 1602, "current_percent": 75, "historical_percent": 105},
        "PNF": {"capacity": 1000, "current_percent": 70, "historical_percent": 102}
    }
    
    return capacity_info.get(station_id, {"capacity": 1000, "current_percent": 50, "historical_percent": 75})

# Process and store reservoir data
def store_reservoir_data(data):
    """Process and store reservoir data in SQLite database"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Log the raw data for debugging
        logger.info(f"Processing data: {json.dumps(data)[:200]}...")
        
        reservoir_id = data.get("cdec_id")
        reservoir_name = data.get("reservoir_name")
        received_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        # Extract relevant data from CDEC response
        raw_data = data.get("data", [])
        if not raw_data:
            logger.warning(f"No data received for {reservoir_name}")
            return
        
        # Process the first data point (most recent)
        data_point = raw_data[0] if isinstance(raw_data, list) else raw_data

        # Log the data point for debugging
        logger.info(f"Data point for {reservoir_id}: {json.dumps(data_point)}")

        # Extract values (adapt these fields based on actual CDEC API response)
        storage_value = data_point.get("value", 0)
        if isinstance(storage_value, str):
            try:
                storage_value = float(storage_value)
            except ValueError:
                storage_value = 0

        if storage_value == -9999:
            # Try to use the most recent valid data from the database
            try:
                prev_conn = sqlite3.connect(DB_NAME)
                prev_cursor = prev_conn.cursor()
                prev_cursor.execute('''
                    SELECT storage_value FROM reservoir_data 
                    WHERE reservoir_id = ? AND storage_value != -9999
                    ORDER BY received_time DESC LIMIT 1
                ''', (reservoir_id,))
                prev_data = prev_cursor.fetchone()
                if prev_data:
                    storage_value = prev_data[0]
                    logger.info(f"Using previous valid data for {reservoir_name}: {storage_value}")
                else:
                    # If no previous valid data, use estimated value based on capacity
                    reservoir_info = get_reservoir_info(reservoir_id)
                    storage_value = reservoir_info["capacity"] * (reservoir_info["current_percent"] / 100)
                    logger.info(f"Using estimated data for {reservoir_name}: {storage_value}")
                prev_conn.close()
            except Exception as e:
                logger.error(f"Error retrieving previous data: {e}")
                # Default to a reasonable value
                storage_value = 0

                
        storage_unit = "TAF"  # Thousand Acre Feet, typical unit for CDEC
        measurement_time = data_point.get("date", received_time)
        
        # Get capacity info
        reservoir_info = get_reservoir_info(reservoir_id)
        percent_capacity = data_point.get("percent_capacity", reservoir_info["current_percent"])
        percent_average = data_point.get("percent_average", reservoir_info["historical_percent"])
        
        # Insert into main reservoir data table
        cursor.execute('''
        INSERT INTO reservoir_data
        (reservoir_id, reservoir_name, storage_value, storage_unit, 
         percent_capacity, percent_average, measurement_time, received_time)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            reservoir_id, reservoir_name, storage_value, storage_unit,
            percent_capacity, percent_average, measurement_time, received_time
        ))
        
        # Update trends table (one entry per day)
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        
        # Check if we already have an entry for this reservoir today
        cursor.execute('''
        SELECT id FROM reservoir_trends 
        WHERE reservoir_id = ? AND date = ?
        ''', (reservoir_id, today))
        
        existing_record = cursor.fetchone()
        
        if existing_record:
            # Update existing record
            cursor.execute('''
            UPDATE reservoir_trends
            SET storage_value = ?, percent_capacity = ?
            WHERE reservoir_id = ? AND date = ?
            ''', (storage_value, percent_capacity, reservoir_id, today))
        else:
            # Insert new record
            cursor.execute('''
            INSERT INTO reservoir_trends
            (reservoir_id, date, storage_value, percent_capacity)
            VALUES (?, ?, ?, ?)
            ''', (reservoir_id, today, storage_value, percent_capacity))
        
        conn.commit()
        logger.info(f"Stored data for {reservoir_name}: {storage_value} {storage_unit}")
        
    except Exception as e:
        logger.error(f"Error storing data: {e}")
        import traceback
        logger.error(traceback.format_exc())
    finally:
        conn.close()

# Setup MQTT client for receiving data
def setup_mqtt_client():
    """Setup and return MQTT client for receiving data"""
    # Define callback functions
    def on_connect(client, userdata, flags, rc, properties=None):
        if rc == 0:
            logger.info("Connected to MQTT Broker!")
            # Subscribe to all reservoir topics
            client.subscribe("reservoir/california/#")
        else:
            logger.error(f"Failed to connect to MQTT Broker! Return code: {rc}")
    
    def on_message(client, userdata, msg):
        try:
            # Decode and parse JSON payload
            payload = json.loads(msg.payload.decode())
            logger.info(f"Received message on {msg.topic}")
            
            # Process and store the data
            store_reservoir_data(payload)
        except json.JSONDecodeError:
            logger.error(f"Failed to decode JSON from message: {msg.payload}")
        except Exception as e:
            logger.error(f"Error processing message: {e}")
    
    # Create MQTT client
    try:
        # Try with MQTTv5 first
        client = mqtt.Client(client_id=CLIENT_ID, protocol=mqtt.MQTTv5)
        
        # Set callbacks
        client.on_connect = on_connect
        client.on_message = on_message
    except Exception as e:
        logger.error(f"Error creating MQTTv5 client: {e}")
        
        # Fall back to MQTTv311
        client = mqtt.Client(client_id=CLIENT_ID)
        
        # Redefine callbacks for MQTTv311
        def on_connect_v3(client, userdata, flags, rc):
            if rc == 0:
                logger.info("Connected to MQTT Broker!")
                client.subscribe("reservoir/california/#")
            else:
                logger.error(f"Failed to connect to MQTT Broker! Return code: {rc}")
        
        client.on_connect = on_connect_v3
        client.on_message = on_message
    
    return client

# Populate initial data
def populate_initial_data():
    """Populate initial data for all reservoirs if the database is empty"""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Check if we have any data
    cursor.execute("SELECT COUNT(*) FROM reservoir_data")
    count = cursor.fetchone()[0]
    conn.close()
    
    if count == 0:
        logger.info("No data in database. Fetching initial data...")
        for station_id, name in RESERVOIRS.items():
            data = fetch_reservoir_data(station_id)
            if data:
                # Create a properly formatted data structure
                formatted_data = {
                    "cdec_id": station_id,
                    "reservoir_name": name,
                    "data": data
                }
                
                # Store the data
                store_reservoir_data(formatted_data)
            else:
                # If API fetch fails, create dummy data from the reservoir info
                info = get_reservoir_info(station_id)
                
                # Create a data structure with minimal required fields
                dummy_data = {
                    "cdec_id": station_id,
                    "reservoir_name": name,
                    "data": [{
                        "date": datetime.datetime.now().strftime("%Y-%m-%d"),
                        "value": info["capacity"] * (info["current_percent"] / 100),
                        "percent_capacity": info["current_percent"],
                        "percent_average": info["historical_percent"]
                    }]
                }
                
                # Store the dummy data
                store_reservoir_data(dummy_data)

# Flask routes for web dashboard
@app.route('/')
def index():
    """Render main dashboard page"""
    return render_template('index.html')

@app.route('/api/reservoirs')
def get_reservoirs():
    """API endpoint to get all reservoir data"""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get latest data for each reservoir
    cursor.execute('''
    SELECT r1.* FROM reservoir_data r1
    JOIN (
        SELECT reservoir_id, MAX(received_time) as max_time
        FROM reservoir_data
        GROUP BY reservoir_id
    ) r2
    ON r1.reservoir_id = r2.reservoir_id AND r1.received_time = r2.max_time
    ORDER BY r1.reservoir_name
    ''')
    
    reservoirs = [dict(row) for row in cursor.fetchall()]
    conn.close()
    
    # Log for debugging
    logger.info(f"Returning {len(reservoirs)} reservoirs")
    
    return jsonify(reservoirs)

@app.route('/api/trends/<reservoir_id>')
def get_reservoir_trends(reservoir_id):
    """API endpoint to get trend data for a specific reservoir"""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get data for the past 30 days
    thirty_days_ago = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
    
    cursor.execute('''
    SELECT * FROM reservoir_trends
    WHERE reservoir_id = ? AND date >= ?
    ORDER BY date ASC
    ''', (reservoir_id, thirty_days_ago))
    
    trends = [dict(row) for row in cursor.fetchall()]
    
    # If no trends, populate with simulated data for demo
    if not trends:
        trends = generate_simulated_trends(reservoir_id, thirty_days_ago)
    
    conn.close()
    
    return jsonify(trends)

# Generate simulated trends for demo
def generate_simulated_trends(reservoir_id, start_date):
    """Generate simulated trend data for demo purposes"""
    trends = []
    info = get_reservoir_info(reservoir_id)
    
    # Generate daily data for 30 days
    start = datetime.datetime.strptime(start_date, "%Y-%m-%d")
    
    for i in range(30):
        date = start + datetime.timedelta(days=i)
        date_str = date.strftime("%Y-%m-%d")
        
        # Generate slightly varying storage values
        variation = (1 + ((i % 5) - 2) / 10)  # Varies between 0.8 and 1.2
        storage = info["capacity"] * (info["current_percent"] / 100) * variation
        percent = info["current_percent"] * variation
        
        trends.append({
            "reservoir_id": reservoir_id,
            "date": date_str,
            "storage_value": storage,
            "percent_capacity": min(percent, 100)  # Cap at 100%
        })
    
    return trends

# Create example HTML template
def create_html_template():
    """Create HTML template for dashboard if it doesn't exist"""
    os.makedirs('templates', exist_ok=True)
    
    html_content = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>California Reservoirs Dashboard</title>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        body {
            font-family: Arial, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
        }
        .header {
            text-align: center;
            margin-bottom: 20px;
        }
        .status-bar {
            background-color: #e3f2fd;
            padding: 10px;
            margin-bottom: 20px;
            border-radius: 5px;
            text-align: center;
        }
        .reservoirs-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 20px;
        }
        .reservoir-card {
            background-color: white;
            border-radius: 8px;
            padding: 15px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            cursor: pointer;
            transition: transform 0.2s;
        }
        .reservoir-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }
        .reservoir-name {
            font-size: 18px;
            font-weight: bold;
            margin-bottom: 10px;
        }
        .storage-value {
            font-size: 24px;
            margin-bottom: 10px;
        }
        .capacity-bar {
            height: 20px;
            background-color: #e0e0e0;
            border-radius: 10px;
            margin-bottom: 10px;
            overflow: hidden;
        }
        .capacity-fill {
            height: 100%;
            background-color: #4CAF50;
            border-radius: 10px;
        }
        .capacity-text {
            margin-top: 5px;
            display: flex;
            justify-content: space-between;
        }
        .trend-container {
            margin-top: 40px;
        }
        .trend-chart {
            background-color: white;
            border-radius: 8px;
            padding: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }
        .metric-label {
            font-size: 14px;
            color: #666;
        }
        .metric-value {
            font-size: 16px;
            font-weight: bold;
        }
        .metrics-row {
            display: flex;
            justify-content: space-between;
            margin-top: 15px;
        }
        .card-footer {
            margin-top: 15px;
            font-size: 12px;
            color: #666;
            text-align: right;
        }
        .debug-info {
            margin-top: 40px;
            background-color: #f8f9fa;
            padding: 15px;
            border-radius: 5px;
            font-family: monospace;
        }
        .debug-info h3 {
            margin-top: 0;
        }
        .debug-toggle {
            margin-top: 20px;
            text-align: center;
        }
        .debug-toggle button {
            background-color: #2196F3;
            color: white;
            border: none;
            padding: 8px 15px;
            border-radius: 4px;
            cursor: pointer;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>California Reservoirs Data Dashboard</h1>
            <p>Current water storage levels and historical trends</p>
        </div>
        
        <div class="status-bar" id="status-bar">
            Last updated: <span id="last-updated">Loading...</span>
            <button onclick="fetchReservoirs()" style="margin-left: 20px;">Refresh Data</button>
        </div>
        
        <div class="reservoirs-grid" id="reservoirs-container">
            <!-- Reservoir cards will be inserted here -->
            <div class="reservoir-card">
                <div class="reservoir-name">Loading data...</div>
            </div>
        </div>
        
        <div class="trend-container">
            <h2>Historical Trend</h2>
            <div class="trend-chart">
                <canvas id="trendChart"></canvas>
            </div>
        </div>
        
        <div class="debug-toggle">
            <button onclick="toggleDebugInfo()">Show Debug Info</button>
        </div>
        
        <div class="debug-info" id="debug-info" style="display: none;">
            <h3>Debug Information</h3>
            <div id="debug-content">No debug info available yet.</div>
        </div>
    </div>

    <script>
        let reservoirData = [];
        
        // Fetch reservoir data
        async function fetchReservoirs() {
            try {
                document.getElementById('status-bar').innerHTML = 'Fetching data...';
                
                const response = await fetch('/api/reservoirs');
                const data = await response.json();
                
                // Store data for debug
                reservoirData = data;
                
                // Update debug info
                updateDebugInfo();
                
                // Display reservoirs
                displayReservoirs(data);
                
                // Update status bar
                document.getElementById('status-bar').innerHTML = 
                    'Last updated: ' + new Date().toLocaleString() + 
                    ' <button onclick="fetchReservoirs()" style="margin-left: 20px;">Refresh Data</button>';
            } catch (error) {
                console.error('Error fetching reservoir data:', error);
                document.getElementById('reservoirs-container').innerHTML = 
                    '<div class="reservoir-card">Error loading data. Please try again later.</div>';
                document.getElementById('status-bar').innerHTML = 
                    'Error updating data. <button onclick="fetchReservoirs()" style="margin-left: 20px;">Try Again</button>';
            }
        }
        
        // Display reservoirs
        function displayReservoirs(reservoirs) {
            const container = document.getElementById('reservoirs-container');
            
            if (!reservoirs.length) {
                container.innerHTML = '<div class="reservoir-card">No reservoir data available.</div>';
                return;
            }
            
            container.innerHTML = '';
            
            reservoirs.forEach(reservoir => {
                const card = document.createElement('div');
                card.className = 'reservoir-card';
                card.setAttribute('data-id', reservoir.reservoir_id);
                
                const percentCapacity = reservoir.percent_capacity || 0;
                const fillColor = getColorForPercentage(percentCapacity);
                
                card.innerHTML = `
                    <div class="reservoir-name">${reservoir.reservoir_name}</div>
                    <div class="storage-value">${Math.round(reservoir.storage_value * 10) / 10} ${reservoir.storage_unit}</div>
                    <div class="capacity-bar">
                        <div class="capacity-fill" style="width: ${percentCapacity}%; background-color: ${fillColor};"></div>
                    </div>
                    <div class="capacity-text">
                        <span>0%</span>
                        <span>${Math.round(percentCapacity)}% of capacity</span>
                        <span>100%</span>
                    </div>
                    <div class="metrics-row">
                        <div>
                            <div class="metric-label">Historical Average</div>
                            <div class="metric-value">${Math.round(reservoir.percent_average || 0)}%</div>
                        </div>
                    </div>
                    <div class="card-footer">
                        Updated: ${new Date(reservoir.measurement_time).toLocaleString()}
                    </div>
                `;
                
                // Add click event
                card.addEventListener('click', () => {
                    fetchTrend(reservoir.reservoir_id, reservoir.reservoir_name);
                });
                
                container.appendChild(card);
            });
            
            // Load first reservoir trend by default
            if (reservoirs.length > 0) {
                fetchTrend(reservoirs[0].reservoir_id, reservoirs[0].reservoir_name);
            }
        }
        
        // Get color based on percentage
        function getColorForPercentage(percentage) {
            if (percentage < 30) return '#F44336';  // Red
            if (percentage < 70) return '#FFC107';  // Amber
            return '#4CAF50';  // Green
        }
        
        // Fetch trend data for a reservoir
        async function fetchTrend(reservoirId, reservoirName) {
            try {
                const response = await fetch(`/api/trends/${reservoirId}`);
                const data = await response.json();
                displayTrend(data, reservoirName);
                
                // Highlight selected reservoir
                document.querySelectorAll('.reservoir-card').forEach(card => {
                    if (card.getAttribute('data-id') === reservoirId) {
                        card.style.border = '2px solid #2196F3';
                    } else {
                        card.style.border = 'none';
                    }
                });
            } catch (error) {
                console.error('Error fetching trend data:', error);
            }
        }
        
        // Display trend chart
        function displayTrend(trendData, reservoirName) {
            const ctx = document.getElementById('trendChart').getContext('2d');
            
            // Destroy previous chart if it exists
            if (window.trendChart) {
                window.trendChart.destroy();
            }
            
            const dates = trendData.map(item => item.date);
            const storageValues = trendData.map(item => item.storage_value);
            const capacityPercentages = trendData.map(item => item.percent_capacity);
            
            window.trendChart = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: dates,
                    datasets: [
                        {
                            label: 'Storage (TAF)',
                            data: storageValues,
                            borderColor: '#2196F3',
                            backgroundColor: 'rgba(33, 150, 243, 0.1)',
                            fill: true,
                            tension: 0.4
                        },
                        {
                            label: 'Capacity (%)',
                            data: capacityPercentages,
                            borderColor: '#4CAF50',
                            backgroundColor: 'rgba(76, 175, 80, 0.1)',
                            fill: true,
                            tension: 0.4,
                            yAxisID: 'y1'
                        }
                    ]
                },
                options: {
                    responsive: true,
                    plugins: {
                        title: {
                            display: true,
                            text: `${reservoirName} - 30 Day Trend`,
                            font: {
                                size: 16
                            }
                        },
                        tooltip: {
                            mode: 'index',
                            intersect: false
                        }
                    },
                    scales: {
                        y: {
                            title: {
                                display: true,
                                text: 'Storage (TAF)'
                            }
                        },
                        y1: {
                            position: 'right',
                            title: {
                                display: true,
                                text: 'Capacity (%)'
                            },
                            min: 0,
                            max: 100,
                            grid: {
                                drawOnChartArea: false
                            }
                        }
                    }
                }
            });
        }
        
        // Toggle debug info
        function toggleDebugInfo() {
            const debugInfo = document.getElementById('debug-info');
            if (debugInfo.style.display === 'none') {
                debugInfo.style.display = 'block';
                updateDebugInfo();
            } else {
                debugInfo.style.display = 'none';
            }
        }
        
        // Update debug info
        function updateDebugInfo() {
            const debugContent = document.getElementById('debug-content');
            debugContent.innerHTML = `
                <p>Number of reservoirs: ${reservoirData.length}</p>
                <p>Raw data:</p>
                <pre>${JSON.stringify(reservoirData, null, 2)}</pre>
            `;
        }
        
        // Load data on page load
        document.addEventListener('DOMContentLoaded', fetchReservoirs);
        
        // Refresh data every 10 minutes
        setInterval(fetchReservoirs, 600000);
    </script>
</body>
</html>
    '''
    
    with open('templates/index.html', 'w') as f:
        f.write(html_content)
    
    logger.info("Created HTML template")

# Main function
def main():
    """Main function to run the data processor and web dashboard"""
    # Setup database
    setup_database()
    
    # Create HTML template
    create_html_template()
    
    # Populate initial data
    populate_initial_data()
    
    # Setup MQTT client
    client = setup_mqtt_client()
    
    try:
        # Connect to MQTT broker
        client.connect(MQTT_BROKER, MQTT_PORT, 60)
        
        # Start MQTT client in a background thread
        client.loop_start()
        
        # Start Flask app
        logger.info("Starting web dashboard on http://127.0.0.1:5000")
        app.run(debug=True, use_reloader=False)
        
    except KeyboardInterrupt:
        logger.info("Stopping application...")
    except Exception as e:
        logger.error(f"Error in main function: {e}")
    finally:
        # Clean up
        client.loop_stop()
        client.disconnect()
        logger.info("Application stopped")

if __name__ == "__main__":
    main()