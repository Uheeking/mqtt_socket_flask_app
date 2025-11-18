import struct
import numpy as np
import paho.mqtt.client as mqtt
import binascii
from datetime import datetime
import os
import csv
import threading
from flask import Flask, render_template_string
from flask_socketio import SocketIO, emit

# ---------------------------- 설정 ----------------------------
BROKER = "192.168.1.4"
PORT = 1883
TOPIC = "D/C65967383F70"
SAMPLING_RATE = 16384 

OUTPUT_DIR = r"C:\Users\유희왕\pr\RunningCode\resetCode\mock_emulator\edited\output"
os.makedirs(OUTPUT_DIR, exist_ok=True)

csv_saved = False  # CSV 한 번만 저장

# ---------------------------- 유틸 함수 ----------------------------
def parse_xyz_from_hex(hex_string):
    xyz_list = []
    data = bytes.fromhex(hex_string)

    for i in range(0, len(data), 6):
        block = data[i:i+6]
        if len(block) < 6:
            break
        x, y, z = struct.unpack('<hhh', block)
        xyz_list.append((x, y, z))

    x_vals = [p[0] for p in xyz_list]
    y_vals = [p[1] for p in xyz_list]
    z_vals = [p[2] for p in xyz_list]

    return x_vals, y_vals, z_vals

def apply_fft(g_values, sampling_rate, step=0.25):
    """
    g_values : FFT에 사용할 데이터
    sampling_rate : 샘플링 레이트 (Hz)
    desired_max_freq : 보고 싶은 최대 주파수 (Hz)
    step : 보간할 주파수 간격
    """
    n = len(g_values)
    if n < 4:
        # 데이터 너무 짧으면 보간 안함
        return [], []

    fft_input = np.array(g_values, dtype=np.float64)
    fft_output = np.fft.rfft(fft_input)
    magnitude = np.abs(fft_output)

    # Nyquist 주파수 = fs/2(최대 주파수)
    nyquist = sampling_rate / 2
    original_freq = np.fft.rfftfreq(n, d=1/sampling_rate)

    # 0부터 Nyquist까지 step 간격으로 목표 주파수 생성
    target_freq = np.arange(0, nyquist + step, step)

    interp = np.interp(target_freq, original_freq, magnitude)
    return target_freq.tolist(), interp.tolist()

def save_csv_once(sensor_hex):
    global csv_saved
    if not csv_saved:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        filename = os.path.join(OUTPUT_DIR, f"raw_{timestamp}.csv")
        with open(filename, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["sensorData"])
            writer.writerow([sensor_hex])
        print(f"💾 CSV 저장 완료: {filename}")
        csv_saved = True

# ---------------------------- Flask + SocketIO ----------------------------
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

@app.route('/')
def index():
    return render_template_string("""
    <html>
      <head>
        <title>FFT Real-Time Plot</title>
        <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
        <script src="https://cdn.socket.io/4.5.4/socket.io.min.js"></script>
      </head>
      <body style="font-family:Arial; margin:30px;">
        <h2>📡 Real-Time FFT Graph (X/Y/Z Separate)</h2>
        <div id="graph" style="width:95%;height:900px;"></div>
        <script>
            const socket = io();

            const layout = {
            title: 'Real-Time FFT (X/Y/Z)',
            grid: {rows: 3, columns: 1, pattern: 'independent'},

            xaxis: {
                title: "Frequency (Hz)",
                range: [0, 50],
                dtick: 0.25,
                tickangle: -45  
            },
            yaxis: {title: "X FFT"},

            xaxis2: {
                title: "Frequency (Hz)",
                range: [0, 50], //FFT에서 계산된 최대 주파수(여기서는 Nyquist = 8192Hz)
                dtick: 0.25,
                tickangle: -45  
            },
            yaxis2: {title: "Y FFT"},

            xaxis3: {
                title: "Frequency (Hz)",
                range: [0, 50],
                dtick: 0.25,
                tickangle: -45  
            },
            yaxis3: {title: "Z FFT"}
        };
            const data = [
                {x: [], y: [], type: 'lines', name: 'X', line: {color: 'blue'}, xaxis: 'x1', yaxis: 'y1'},
                {x: [], y: [], type: 'lines', name: 'Y', line: {color: 'green'}, xaxis: 'x2', yaxis: 'y2'},
                {x: [], y: [], type: 'lines', name: 'Z', line: {color: 'red'}, xaxis: 'x3', yaxis: 'y3'}
            ];

            Plotly.newPlot('graph', data, layout);

            socket.on('update_plot', (msg) => {
            const data = [
                {x: msg.x_freq, y: msg.x_fft, type: 'lines', name: 'X', line: {color: 'blue'}},
                {x: msg.y_freq, y: msg.y_fft, type: 'lines', name: 'Y', line: {color: 'green'}},
                {x: msg.z_freq, y: msg.z_fft, type: 'lines', name: 'Z', line: {color: 'red'}}
            ];
            Plotly.react('graph', [
            {x: msg.x_freq, y: msg.x_fft, type: 'lines', name: 'X', line:{color:'blue'}, xaxis: 'x1', yaxis:'y1'},
            {x: msg.y_freq, y: msg.y_fft, type: 'lines', name: 'Y', line:{color:'green'}, xaxis: 'x2', yaxis:'y2'},
            {x: msg.z_freq, y: msg.z_fft, type: 'lines', name: 'Z', line:{color:'red'}, xaxis: 'x3', yaxis:'y3'}
        ], layout);
        });
        </script>
      </body>
    </html>
    """)

# ---------------------------- MQTT 콜백 ----------------------------
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("✅ MQTT Connected!")
        client.subscribe(TOPIC)
        print(f"📡 Subscribed to: {TOPIC}")
    else:
        print(f"❌ MQTT 연결 실패 (코드 {rc})")

def on_message(client, userdata, msg):
    payload = msg.payload
    hex_str = binascii.hexlify(payload).decode()
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    print(f"\n📩 Received MQTT Data ({len(payload)} bytes) time {timestamp}")
    print(f"HEX: {hex_str[:100]}...")

    save_csv_once(hex_str)

    x_vals, y_vals, z_vals = parse_xyz_from_hex(hex_str)
    x_freq, x_fft = apply_fft(x_vals, sampling_rate=SAMPLING_RATE)
    y_freq, y_fft = apply_fft(y_vals, sampling_rate=SAMPLING_RATE)
    z_freq, z_fft = apply_fft(z_vals, sampling_rate=SAMPLING_RATE)

    if x_fft and y_fft and z_fft:
        socketio.emit("update_plot", {
            "x_freq": x_freq,
            "y_freq": y_freq,
            "z_freq": z_freq,
            "x_fft": x_fft,
            "y_fft": y_fft,
            "z_fft": z_fft
        })

def on_disconnect(client, userdata, rc):
    print("🔌 MQTT disconnected.")

# ---------------------------- MQTT 스레드 ----------------------------
def mqtt_thread():
    client = mqtt.Client()
    client.on_connect = on_connect
    client.on_message = on_message
    client.on_disconnect = on_disconnect
    client.connect(BROKER, PORT, 60)
    client.loop_forever()

# ---------------------------- 메인 실행 ----------------------------
if __name__ == "__main__":
    print("🚀 Starting Flask + SocketIO...")

    # MQTT 백그라운드 스레드
    threading.Thread(target=mqtt_thread, daemon=True).start()

    # Flask 서버
    socketio.run(app, host="0.0.0.0", port=5000)
