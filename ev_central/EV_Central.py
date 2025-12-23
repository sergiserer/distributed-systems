import sys
import time
import queue
import json
import threading
import sqlite3
import socket
import os
import logging
from datetime import datetime
from kafka import KafkaConsumer, KafkaProducer
from flask import Flask, jsonify, request
from flask_cors import CORS
from cryptography.fernet import Fernet 


# --- CONFIGURACIÓN ---
DB_NAME = 'ev_central.db'
SOCKET_HOST = '0.0.0.0'
SOCKET_PORT = 8000
API_PORT = 5000
KAFKA_SERVER = '192.168.1.24:9092'  # REVISA QUE ESTA IP SEA LA DE TU DOCKER
HEARTBEAT_TIMEOUT = 15

# --- VARIABLES GLOBALES ---
active_socket_connections = {} 
connections_lock = threading.Lock()
producer = None        
gui_queue_global = None 
weather_state_cache = {} # Memoria de clima
weather_temp_cache = {} 
live_telemetry_cache = {} # <--- NUEVO: Memoria para telemetría en vivo

# --- INICIALIZACIÓN FLASK ---
app_flask = Flask(__name__)
CORS(app_flask) 
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR) 

# -------------------------------------------------------------------------
# SISTEMA DE AUDITORÍA
# -------------------------------------------------------------------------
def log_audit(source_ip, action, description):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"{timestamp} | IP: {source_ip} | ACTION: {action} | DESC: {description}"
    
    print(f"🔒 [AUDIT] {entry}")
    try:
        with open("system_audit.log", "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception as e:
        print(f"Error escribiendo auditoría: {e}")

    if gui_queue_global:
        gui_queue_global.put(("ADD_AUDIT", timestamp, source_ip, action, description))

# -------------------------------------------------------------------------
# FUNCIONES BBDD
# -------------------------------------------------------------------------
def get_db_connection():
    conn = sqlite3.connect(DB_NAME, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def clear_cp_credentials_in_db(cp_id):
    conn = get_db_connection()
    try:
        conn.execute("UPDATE ChargingPoints SET encryption_key = NULL, status = 'DESCONECTADO' WHERE cp_id = ?", (cp_id,))
        conn.commit()
    except Exception as e:
        print(f"[DB] Error clearing credentials: {e}")
    finally:
        conn.close()

def update_cp_status_in_db(cp_id, new_status):
    conn = None
    try:
        conn = get_db_connection()
        conn.execute("UPDATE ChargingPoints SET status = ?, last_update = CURRENT_TIMESTAMP WHERE cp_id = ?", (new_status, cp_id))
        conn.commit()
    except sqlite3.Error as e:
        print(f"[DB_ERROR] Update status: {e}")
    finally:
        if conn: conn.close()

def register_cp_in_db(cp_id, location, price):
    conn = None
    try:
        conn = get_db_connection()
        conn.execute("""
            INSERT INTO ChargingPoints (cp_id, location, price_kwh, status, last_heartbeat, last_update)
            VALUES (?, ?, ?, 'DESCONECTADO', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(cp_id) DO UPDATE SET 
                location = excluded.location,
                price_kwh = excluded.price_kwh,
                last_update = CURRENT_TIMESTAMP
        """, (cp_id, location, price))
        conn.commit()
    except sqlite3.Error as e:
        print(f"[DB_ERROR] Register CP: {e}")
    finally:
        if conn: conn.close()
            
def update_cp_heartbeat(cp_id):
    conn = None
    try:
        conn = get_db_connection()
        conn.execute("UPDATE ChargingPoints SET last_heartbeat = CURRENT_TIMESTAMP WHERE cp_id = ?", (cp_id,))
        conn.commit()
    except sqlite3.Error as e:
        print(f"[DB_ERROR] Heartbeat: {e}")
    finally:
        if conn: conn.close()

def get_cp_info_from_db(cp_id):
    conn = None
    info = {'status': None, 'price_kwh': 0.50, 'encryption_key': None} 
    try:
        conn = get_db_connection()
        cursor = conn.execute("SELECT status, price_kwh, encryption_key FROM ChargingPoints WHERE cp_id = ?", (cp_id,))
        result = cursor.fetchone()
        if result:
            info['status'] = result['status']
            info['price_kwh'] = result['price_kwh']
            info['encryption_key'] = result['encryption_key']
    except sqlite3.Error:
        pass
    finally:
        if conn: conn.close()
    return info

def get_charge_history_for_driver(driver_id):
    logs = []
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.execute("SELECT * FROM ChargeLog WHERE driver_id = ? ORDER BY start_time DESC LIMIT 10", (driver_id,))
        for row in cursor.fetchall():
            logs.append(dict(row))
    except sqlite3.Error:
        pass
    finally:
        if conn: conn.close()
    return logs

def get_all_cps_status():
    cps = []
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.execute("SELECT cp_id, location, status, price_kwh, last_update FROM ChargingPoints")
        for row in cursor.fetchall():
            cp_data = dict(row)

            # --- INYECTAR TEMPERATURA SI LA TENEMOS ---
            cp_loc_lower = cp_data['location'].lower()
            current_temp = None
            for city, temp in weather_temp_cache.items():
                if city in cp_loc_lower:
                    current_temp = temp
                    break
            cp_data['temp'] = current_temp 
            
            # --- INYECTAR TELEMETRÍA EN VIVO (NUEVO) ---
            cp_id = cp_data['cp_id']
            if cp_id in live_telemetry_cache:
                cp_data['live_data'] = live_telemetry_cache[cp_id]
            else:
                cp_data['live_data'] = None
            # -------------------------------------------

            cps.append(cp_data)
    finally:
        if conn: conn.close()
    return cps

def get_all_encryption_keys():
    keys = {}
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.execute("SELECT cp_id, encryption_key FROM ChargingPoints WHERE encryption_key IS NOT NULL")
        for row in cursor.fetchall():
            keys[row['cp_id']] = row['encryption_key']
    except: pass
    finally:
        if conn: conn.close()
    return keys

# --- REVOCACIÓN REAL ---
def revoke_cp_key_in_db(cp_id):
    """Borra la clave de la BD y PATEA al usuario del socket."""
    global active_socket_connections
    
    # 1. Borrar de BD
    clear_cp_credentials_in_db(cp_id)
    print(f"🚫 [SEGURIDAD] Clave del CP {cp_id} eliminada de BBDD.")
    
    # 2. ECHAR AL USUARIO DEL SOCKET
    with connections_lock:
        if cp_id in active_socket_connections:
            try:
                conn = active_socket_connections[cp_id]
                conn.sendall(b"REVOKED\n") 
                conn.close()
                del active_socket_connections[cp_id]
                print(f"🔌 [SEGURIDAD] Socket de {cp_id} cerrado forzosamente.")
            except Exception as e:
                print(f"Error cerrando socket: {e}")

def broadcast_status_change(kafka_prod, cp_id, new_status, location=None, price=None):
    if not kafka_prod: return
    payload = {'cp_id': cp_id, 'status': new_status}
    if location: payload['location'] = location
    if price: payload['price_kwh'] = price
    try:
        kafka_prod.send('topic_status_broadcast', payload)
        kafka_prod.flush()
    except Exception as e:
        print(f"[KAFKA ERROR] Broadcast failed: {e}")

# -------------------------------------------------------------------------
# LÓGICA DE CONTROL GLOBAL
# -------------------------------------------------------------------------
def send_admin_command_global(cp_id, new_status, socket_cmd, extra_gui_data=None):
    global producer, active_socket_connections, gui_queue_global
    target_conn = None
    with connections_lock:
        target_conn = active_socket_connections.get(cp_id)
        
    if target_conn:
        try:
            target_conn.sendall(f"{socket_cmd}\n".encode('utf-8'))
        except Exception as e:
            print(f"[API] Error socket {cp_id}: {e}")
    
    update_cp_status_in_db(cp_id, new_status)
    broadcast_status_change(producer, cp_id, new_status)
        
    if gui_queue_global:
        gui_queue_global.put(("UPDATE_CP", cp_id, new_status, extra_gui_data))
        # Auditamos el cambio
        if new_status == 'PARADO':
             log_audit("SISTEMA", "STATUS_CHANGE", f"{cp_id} detenido (PARADO)")
        elif new_status == 'ACTIVADO':
             log_audit("SISTEMA", "STATUS_CHANGE", f"{cp_id} activado")
    
    return True

# -------------------------------------------------------------------------
# FUNCIONES AUXILIARES CLIMA
# -------------------------------------------------------------------------
def is_location_cold_cached(cp_location):
    global weather_state_cache
    if not cp_location: return False
    
    loc_lower = cp_location.lower()
    for city, is_cold in weather_state_cache.items():
        if city in loc_lower and is_cold:
            return True
    return False

# -------------------------------------------------------------------------
# API REST FLASK
# -------------------------------------------------------------------------
@app_flask.route('/api/cps', methods=['GET'])
def api_list_cps():
    return jsonify(get_all_cps_status())

@app_flask.route('/api/internal/delete', methods=['POST'])
def api_internal_delete():
    data = request.json
    cp_id = data.get('cp_id')
    revoke_cp_key_in_db(cp_id)
    return jsonify({"status": "OK"}), 200

@app_flask.route('/api/internal/register', methods=['POST'])
def api_internal_register():
    data = request.json
    try:
        conn = get_db_connection()
        conn.execute("""
            INSERT INTO ChargingPoints (cp_id, location, price_kwh, status, last_update, encryption_key)
            VALUES (?, ?, ?, 'DESCONECTADO', CURRENT_TIMESTAMP, ?)
            ON CONFLICT(cp_id) DO UPDATE SET
                location = excluded.location,
                price_kwh = excluded.price_kwh,
                encryption_key = excluded.encryption_key,
                last_update = CURRENT_TIMESTAMP
        """, (data['cp_id'], data['location'], data['price'], data['encryption_key']))
        conn.commit()
        conn.close()
        print(f"💾 [DB] Guardado remoto desde Registry: CP {data['cp_id']}")
        return jsonify({"status": "OK"}), 200
    except Exception as e:
        print(f"❌ [DB ERROR] Fallo al guardar desde Registry: {e}")
        return jsonify({"error": str(e)}), 500

@app_flask.route('/api/audit', methods=['GET'])
def api_get_audit():
    logs = []
    try:
        if os.path.exists("system_audit.log"):
            with open("system_audit.log", "r", encoding="utf-8") as f:
                lines = f.readlines()
                for line in lines[-20:]: 
                    parts = line.strip().split(" | ")
                    if len(parts) >= 4:
                        logs.append({
                            "time": parts[0],
                            "ip": parts[1].replace("IP: ", ""),
                            "action": parts[2].replace("ACTION: ", ""),
                            "desc": parts[3].replace("DESC: ", "")
                        })
    except Exception as e:
        print(f"Error leyendo logs para API: {e}")
    return jsonify(logs[::-1])

@app_flask.route('/api/alert/weather', methods=['POST'])
def api_weather_alert():
    global weather_state_cache, weather_temp_cache

    data = request.json
    location_alert = data.get('location', '').strip().lower()
    is_cold = data.get('alert', False)
    temp_val = data.get('temp', None)
    requester_ip = request.remote_addr 

    # 1. GUARDAR LA TEMPERATURA EXACTA
    if temp_val is not None:
        weather_temp_cache[location_alert] = temp_val

    # 2. LOGICA DE ALERTAS
    last_state = weather_state_cache.get(location_alert)

    if last_state == is_cold:
        return jsonify({"status": "unchanged", "temp": temp_val}), 200

    weather_state_cache[location_alert] = is_cold
    print(f"🌍 [API CLIMA] CAMBIO DETECTADO en '{location_alert}': Frío={is_cold}, Temp={temp_val}")

    audit_msg = f"INICIO TEMPORAL ({temp_val}ºC)" if is_cold else f"FIN TEMPORAL ({temp_val}ºC)"
    log_audit(requester_ip, "WEATHER_CHANGE", f"{audit_msg} en {location_alert}")

    # Buscar CPs afectados y actuar
    conn = get_db_connection()
    cursor = conn.execute("SELECT cp_id, location, status FROM ChargingPoints")
    affected = []
    for row in cursor.fetchall():
        if location_alert in row['location'].lower():
            affected.append(dict(row))
    conn.close()

    actions = []
    gui_info = {'temp': temp_val} 

    for cp in affected:
        cp_id = cp['cp_id']
        status = cp['status']

        if is_cold:
            if status == 'SUMINISTRANDO':
                send_admin_command_global(cp_id, 'PARADO', 'STOP_CP', gui_info)
                log_audit("SISTEMA", "CHARGE_ABORT", f"Carga interrumpida en {cp_id} por temporal.")
                actions.append(f"{cp_id} CHARGE ABORTED")
            elif status in ('ACTIVADO', 'ESPERANDO_INICIO'):
                send_admin_command_global(cp_id, 'PARADO', 'STOP_CP', gui_info)
                log_audit("SISTEMA", "WEATHER_STOP", f"{cp_id} deshabilitado por precaución.")
                actions.append(f"{cp_id} STOPPED")
        else:
            if status == 'PARADO':
                send_admin_command_global(cp_id, 'ACTIVADO', 'RESUME_CP')
                log_audit("SISTEMA", "WEATHER_RESUME", f"{cp_id} reactivado.")
                actions.append(f"{cp_id} RESUMED")

    return jsonify({"status": "processed", "actions": actions})

# --- ADMIN ENDPOINTS ---

@app_flask.route('/api/admin/stop', methods=['POST'])
def api_admin_stop():
    data = request.json
    cp_id = data.get('cp_id')
    if not cp_id: return jsonify({"error": "Falta CP ID"}), 400
    
    current_info = get_cp_info_from_db(cp_id)
    if current_info['status'] == 'DESCONECTADO':
        return jsonify({"error": f"⚠️ No se puede parar {cp_id} porque está DESCONECTADO."}), 400

    requester_ip = request.remote_addr
    log_audit(requester_ip, "ADMIN_STOP_WEB", f"Parada manual desde Web: {cp_id}")
    
    send_admin_command_global(cp_id, 'PARADO', 'STOP_CP')
    return jsonify({"status": "OK", "message": f"{cp_id} detenido."}), 200

@app_flask.route('/api/admin/revoke', methods=['POST'])
def api_admin_revoke():
    data = request.json
    cp_id = data.get('cp_id')
    if not cp_id: return jsonify({"error": "Falta CP ID"}), 400
    
    requester_ip = request.remote_addr
    log_audit(requester_ip, "ADMIN_REVOKE_WEB", f"Revocación clave desde Web: {cp_id}")
    
    revoke_cp_key_in_db(cp_id)
    
    if gui_queue_global:
        gui_queue_global.put(("ADD_MESSAGE", f"⚠️ WEB ADMIN: Clave de {cp_id} REVOCADA."))
        
    return jsonify({"status": "OK", "message": f"Clave de {cp_id} revocada."}), 200

@app_flask.route('/api/admin/resume', methods=['POST'])
def api_admin_resume():
    data = request.json
    cp_id = data.get('cp_id')
    if not cp_id: return jsonify({"error": "Falta CP ID"}), 400
    
    current_info = get_cp_info_from_db(cp_id)
    if current_info['status'] == 'DESCONECTADO':
        return jsonify({"error": f"⚠️ No se puede reanudar {cp_id} porque está DESCONECTADO."}), 400

    # COMPROBACIÓN CLIMA (WEB)
    conn = get_db_connection()
    row = conn.execute("SELECT location FROM ChargingPoints WHERE cp_id=?", (cp_id,)).fetchone()
    conn.close()
    
    if row and is_location_cold_cached(row['location']):
        return jsonify({"error": f"❄️ IMPOSIBLE: Alerta climática activa en {cp_id}."}), 409

    requester_ip = request.remote_addr
    log_audit(requester_ip, "ADMIN_RESUME_WEB", f"Reanudación manual desde Web: {cp_id}")
    
    send_admin_command_global(cp_id, 'ACTIVADO', 'RESUME_CP')
    
    return jsonify({"status": "OK", "message": f"{cp_id} reanudado y operativo."}), 200

def run_flask_server():
    app_flask.run(host='0.0.0.0', port=API_PORT, debug=False, use_reloader=False)

# -------------------------------------------------------------------------
# SERVIDOR SOCKETS
# -------------------------------------------------------------------------
def handle_socket_client(conn, addr, producer, gui_queue):
    global active_socket_connections
    source_ip = addr[0]
    print(f"[SOCKET] Conexión: {addr}")
    
    cp_id = None
    try:
        data = conn.recv(1024).decode('utf-8').strip()
        if not data: return
        parts = data.split('\n')[0].split(';') 
        cmd = parts[0]

        if cmd == 'REGISTER':
            cp_id = parts[1]
            cp_location = parts[2]
            
            # 1. Registro en DB
            register_cp_in_db(cp_id, cp_location, float(parts[3]))
            
            # 2. ACTIVADO por defecto al reconectar
            update_cp_status_in_db(cp_id, "ACTIVADO")
            broadcast_status_change(producer, cp_id, "ACTIVADO")
            
            cp_gui_data = {
                "id": cp_id,
                "loc": cp_location,
                "price": f"{float(parts[3]):.2f}€/kWh"
            }
            gui_queue.put(("NEW_CP", cp_gui_data))
            gui_queue.put(("UPDATE_CP", cp_id, "ACTIVADO", None))
            
            conn.send(b"ACK;REGISTER_OK\n")
            
            with connections_lock:
                active_socket_connections[cp_id] = conn
            
            log_audit(source_ip, "AUTH_CP", f"CP {cp_id} reconectado y activo")

            # Check Clima
            if is_location_cold_cached(cp_location):
                print(f"❄️ [CLIMA] CP {cp_id} alerta activa (Previo).")
                time.sleep(0.5) 
                send_admin_command_global(
                    cp_id, 
                    'PARADO', 
                    'STOP_CP', 
                    {'temp': 'PREVIO'}
                )
                log_audit("SISTEMA", "WEATHER_BLOCK", f"{cp_id} bloqueado por alerta.")
            
        elif cmd == 'GET_HISTORY':
            driver_id = parts[1]
            logs = get_charge_history_for_driver(driver_id)
            conn.sendall(json.dumps(logs).encode('utf-8'))
            conn.close()
            log_audit(source_ip, "DATA_ACCESS", f"Driver {driver_id} solicitó historial")
            return
        else:
            conn.close()
            return

        while True:
            data = conn.recv(1024).decode('utf-8')
            if not data: break
            
            for line in data.strip().split('\n'):
                if not line: continue
                parts = line.split(';')
                
                if parts[0] == 'HEARTBEAT':
                    update_cp_heartbeat(cp_id)
                    conn.send(b"ACK;HEARTBEAT_OK\n")
                
                elif parts[0] == 'STATUS':
                    new_status = parts[1]
                    update_cp_status_in_db(cp_id, new_status)
                    if new_status == 'ACTIVADO': update_cp_heartbeat(cp_id)
                    
                    broadcast_status_change(producer, cp_id, new_status)
                    gui_queue.put(("UPDATE_CP", cp_id, new_status, None))
                    conn.send(b"ACK;STATUS_UPDATED\n")

    except Exception as e:
        print(f"[SOCKET] Error {addr}: {e}")
    finally:
        if cp_id:
            print(f"[SOCKET] Cerrando {cp_id}")
            update_cp_status_in_db(cp_id, "DESCONECTADO")
            broadcast_status_change(producer, cp_id, "DESCONECTADO")
            gui_queue.put(("UPDATE_CP", cp_id, "DESCONECTADO", None))
            with connections_lock:
                active_socket_connections.pop(cp_id, None)
        conn.close()

def start_socket_server(gui_queue):
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((SOCKET_HOST, SOCKET_PORT))
    server.listen(5)
    print(f"OK [SOCKETS] Escuchando en {SOCKET_PORT}...")
    while True:
        conn, addr = server.accept()
        threading.Thread(target=handle_socket_client, args=(conn, addr, producer, gui_queue), daemon=True).start()

# -------------------------------------------------------------------------
# KAFKA LISTENER
# -------------------------------------------------------------------------
def start_kafka_listener(gui_queue):
    global live_telemetry_cache
    try:
        consumer = KafkaConsumer(
            'topic_requests', 'topic_data_streaming',
            bootstrap_servers=KAFKA_SERVER,
            auto_offset_reset='latest'
        )
        print("OK [KAFKA] Listener iniciado (Modo Seguro).")

        for msg in consumer:
            try:
                data = None
                raw_bytes = msg.value
                
                # DESCIFRADO
                if msg.topic == 'topic_data_streaming':
                    decrypted = False
                    all_keys = get_all_encryption_keys()
                    for cp_id, key_str in all_keys.items():
                        try:
                            f = Fernet(key_str.encode('utf-8'))
                            decoded_bytes = f.decrypt(raw_bytes)
                            data = json.loads(decoded_bytes.decode('utf-8'))
                            decrypted = True
                            break
                        except: continue
                    
                    if not decrypted:
                        try: data = json.loads(raw_bytes.decode('utf-8'))
                        except: continue
                else:
                    try: data = json.loads(raw_bytes.decode('utf-8'))
                    except: continue

                if not data: continue

                if msg.topic == 'topic_requests':
                    cp_id, driver = data['cp_id'], data['driver_id']
                    cp_info = get_cp_info_from_db(cp_id)
                    status = cp_info.get('status')
                    gui_queue.put(("ADD_REQUEST", datetime.now().strftime("%d/%m"), datetime.now().strftime("%H:%M"), driver, cp_id))

                    if status == 'ACTIVADO':
                        update_cp_status_in_db(cp_id, 'ESPERANDO_INICIO')
                        broadcast_status_change(producer, cp_id, 'ESPERANDO_INICIO')
                        producer.send(f'topic_commands_{cp_id}', {
                            'action': 'START_CHARGE', 'driver_id': driver, 'price_kwh': cp_info['price_kwh']
                        })
                        if 'response_topic' in data:
                            producer.send(data['response_topic'], {'status': 'APPROVED', 'cp_id': cp_id})
                        gui_queue.put(("UPDATE_CP", cp_id, "ESPERANDO_INICIO", None))
                        log_audit("KAFKA", "CHARGE_APPROVED", f"Carga aprobada {driver}@{cp_id}")
                    else:
                        if 'response_topic' in data:
                            producer.send(data['response_topic'], {'status': 'DENIED', 'cp_id': cp_id, 'reason': status})
                        log_audit("KAFKA", "CHARGE_DENIED", f"Carga denegada {driver}@{cp_id} ({status})")

                elif msg.topic == 'topic_data_streaming':
                    status, cp_id = data.get('status'), data.get('cp_id')
                    
                    if status == 'SUMINISTRANDO':
                        conn = get_db_connection()
                        curr = conn.execute("SELECT status FROM ChargingPoints WHERE cp_id=?", (cp_id,)).fetchone()
                        conn.close()
                        if curr and curr['status'] != 'SUMINISTRANDO':
                            update_cp_status_in_db(cp_id, 'SUMINISTRANDO')
                            broadcast_status_change(producer, cp_id, 'SUMINISTRANDO')
                        
                        # --- ACTUALIZAR CACHE DE TELEMETRÍA (NUEVO) ---
                        live_telemetry_cache[cp_id] = {
                            'kwh': data.get('kwh', 0),
                            'euros': data.get('euros', 0)
                        }
                        # ---------------------------------------------
                        
                        gui_data = {"driver": data.get('driver_id'), "kwh": f"{data.get('kwh',0):.1f}", "eur": f"{data.get('euros',0):.2f}"}
                        gui_queue.put(("UPDATE_CP", cp_id, "SUMINISTRANDO", gui_data))
                    
                    elif status in ('FINALIZADO', 'FINALIZADO_AVERIA', 'FINALIZADO_PARADA'):
                        # --- LIMPIAR CACHE DE TELEMETRÍA (NUEVO) ---
                        if cp_id in live_telemetry_cache:
                            del live_telemetry_cache[cp_id]
                        # -------------------------------------------

                        try:
                            c = get_db_connection()
                            c.execute("INSERT INTO ChargeLog (cp_id, driver_id, start_time, end_time, total_kwh, total_euros) VALUES (?,?,?,?,?,?)",
                                (cp_id, data.get('driver_id'), data.get('start_time'), data.get('end_time'), data.get('total_kwh'), data.get('total_euros')))
                            c.commit()
                            c.close()
                        except: pass

                        current_info = get_cp_info_from_db(cp_id)
                        
                        if current_info['encryption_key'] is None:
                            new_st = 'DESCONECTADO'
                        else:
                            new_st = 'ACTIVADO'
                            if status == 'FINALIZADO_AVERIA': new_st = 'AVERIADO'
                            elif status == 'FINALIZADO_PARADA': new_st = 'PARADO'
                        
                        update_cp_status_in_db(cp_id, new_st)
                        broadcast_status_change(producer, cp_id, new_st)
                        gui_queue.put(("UPDATE_CP", cp_id, new_st, None))

            except Exception as e:
                print(f"Error Kafka Msg: {e}")
    except Exception as e:
        print(f"Error fatal Kafka Listener: {e}")

# -------------------------------------------------------------------------
# MAIN
# -------------------------------------------------------------------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.append(PARENT_DIR)
from central_gui import CentralApp

class BackendConnector:
    def __init__(self, gui_queue): 
        self.gui_queue = gui_queue
    
    def request_parar_cp(self, cp_id):
        log_audit("LOCALHOST", "ADMIN_STOP", f"Parada manual {cp_id}")
        send_admin_command_global(cp_id, 'PARADO', 'STOP_CP')
        
    def request_reanudar_cp(self, cp_id):
        conn = get_db_connection()
        row = conn.execute("SELECT location FROM ChargingPoints WHERE cp_id=?", (cp_id,)).fetchone()
        conn.close()
        
        if row and is_location_cold_cached(row['location']):
            log_audit("LOCALHOST", "ADMIN_RESUME_BLOCK", f"Reanudación {cp_id} bloqueada por clima")
            if self.gui_queue:
                self.gui_queue.put(("ADD_MESSAGE", f"❄️ IMPOSIBLE: Alerta clima activa en {cp_id}."))
            return

        log_audit("LOCALHOST", "ADMIN_RESUME", f"Reanudación manual {cp_id}")
        send_admin_command_global(cp_id, 'ACTIVADO', 'RESUME_CP')

    def request_revocar_clave(self, cp_id):
        log_audit("LOCALHOST", "ADMIN_REVOKE", f"Revocación de clave {cp_id}")
        revoke_cp_key_in_db(cp_id)
        if self.gui_queue:
            self.gui_queue.put(("ADD_MESSAGE", f"🚫 SEGURIDAD: Clave de {cp_id} REVOCADA."))

if __name__ == "__main__":
    if not os.path.exists(DB_NAME):
        import init_db
        init_db.create_tables()

    try:
        print("🧹 Limpiando estados antiguos...")
        conn_init = sqlite3.connect(DB_NAME)
        conn_init.execute("UPDATE ChargingPoints SET status = 'DESCONECTADO'")
        conn_init.commit()
        conn_init.close()
    except Exception as e:
        print(f" Warning Limpieza: {e}")

    try:
        producer = KafkaProducer(
            bootstrap_servers=KAFKA_SERVER, 
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )
    except Exception as e:
        print(f"FATAL: Kafka no disponible ({e}).")
        sys.exit(1)

    gui_queue = queue.Queue()
    gui_queue_global = gui_queue

    threading.Thread(target=start_socket_server, args=(gui_queue,), daemon=True).start()
    threading.Thread(target=start_kafka_listener, args=(gui_queue,), daemon=True).start()
    threading.Thread(target=run_flask_server, daemon=True).start() 

    app = CentralApp(gui_queue)
    connector = BackendConnector(gui_queue)
    app.set_controller(connector)
    
    raw_cps = get_all_cps_status()
    gui_cps = []
    for i, cp in enumerate(raw_cps):
        gui_cps.append({
            "id": cp['cp_id'],
            "loc": cp['location'],
            "price": f"{cp['price_kwh']:.2f}€/kWh",
            "grid_row": i // 5,
            "grid_col": i % 5
        })
    app.load_initial_cps(gui_cps) 
    
    print("--- CENTRAL READY (Release 2 Complete) ---")
    app.mainloop()