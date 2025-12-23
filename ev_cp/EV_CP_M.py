import socket
import threading
import time
import argparse
import sys
import requests
import urllib3
import random
import json
import os

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- VARIABLES GLOBALES ---
central_conn = None
central_lock = threading.Lock()
cp_id_global = None
last_reported_status = "DESCONECTADO" 
engine_conn = None
engine_lock = threading.Lock()
status_lock = threading.Lock()     

auth_token = None
encryption_key = None

def get_creds_filename(cp_id):
    return f"creds_{cp_id}.json"

def save_credentials(cp_id, token, key):
    filename = get_creds_filename(cp_id)
    data = {"auth_token": token, "encryption_key": key}
    try:
        with open(filename, 'w') as f:
            json.dump(data, f)
        print(" Credenciales guardadas en disco.")
    except Exception as e:
        print(f"Error guardando credenciales: {e}")

def load_credentials(cp_id):
    filename = get_creds_filename(cp_id)
    if os.path.exists(filename):
        try:
            with open(filename, 'r') as f:
                data = json.load(f)
            print(" Credenciales cargadas desde disco.")
            return data.get("auth_token"), data.get("encryption_key")
        except:
            return None, None
    return None, None

def delete_credentials(cp_id):
    filename = get_creds_filename(cp_id)
    if os.path.exists(filename):
        os.remove(filename)
        print(" Credenciales borradas del disco.")

# --- HELPERS ---
def api_registry_request(method, ip, port, endpoint, payload=None):
    base_url = f"https://{ip}:{port}/api"
    try:
        if method == "POST":
            response = requests.post(f"{base_url}/{endpoint}", json=payload, timeout=5, verify=False)
        elif method == "DELETE":
            response = requests.delete(f"{base_url}/{endpoint}", timeout=5, verify=False)
        return response
    except Exception as e:
        print(f" Error conexión Registry: {e}")
        return None

def send_to_central(message_str):
    global central_conn, central_lock
    with central_lock:
        if not central_conn: return False
        try:
            central_conn.sendall(f"{message_str}\n".encode('utf-8'))
            return True
        except:
            return False

def send_to_engine(message_str):
    global engine_conn, engine_lock
    with engine_lock:
        if not engine_conn: return False
        try:
            engine_conn.sendall(f"{message_str}\n".encode('utf-8'))
            return True
        except:
            return False

def health_check_loop(engine_ip, engine_port, cp_id):
    global last_reported_status, engine_conn, encryption_key
    while True:
        # Intento de reconexión con Engine
        if not engine_conn:
            try:
                conn_obj = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                conn_obj.settimeout(5)
                conn_obj.connect((engine_ip, engine_port))
                conn_obj.sendall(f"ID;{cp_id}\n".encode('utf-8'))
                resp = conn_obj.recv(1024).decode().strip()
                if resp == "ID_OK":
                    if encryption_key:
                        conn_obj.sendall(f"KEY;{encryption_key}\n".encode('utf-8'))
                    conn_obj.settimeout(None)
                    with engine_lock: engine_conn = conn_obj
                else:
                    conn_obj.close()
            except: pass
        
        # Ping al Engine y reporte a Central
        if engine_conn:
            try:
                engine_conn.sendall(b"PING\n")
                status = engine_conn.recv(1024).decode().strip()
                new_status = "ACTIVADO" if status == "OK" else "AVERIADO"
                with status_lock:
                    if new_status != last_reported_status and last_reported_status != "PARADO":
                        if send_to_central(f"STATUS;{new_status}"):
                            last_reported_status = new_status
            except:
                with engine_lock: engine_conn = None
        time.sleep(2)

def central_listener_thread(sock, cp_id):
    global central_conn, auth_token, encryption_key, last_reported_status
    print("\n Escuchando órdenes de Central en segundo plano...")
    
    try:
        while True:
            data = sock.recv(1024)
            if not data: 
                break
            
            cmds = data.decode().strip().split('\n')
            for cmd in cmds:
                if cmd == "STOP_CP":
                    print("\n [ALERTA] ORDEN CENTRAL: PARAR.")
                    send_to_engine("FORCE_STOP")
                    with status_lock: last_reported_status = "PARADO"
                    
                elif cmd == "RESUME_CP":
                    print("\n [ALERTA] ORDEN CENTRAL: REANUDAR.")
                    send_to_engine("FORCE_RESUME")
                    with status_lock: last_reported_status = "ACTIVADO"
                    
                elif cmd == "REVOKED":
                    print("\n [ALERTA] SEGURIDAD: CLAVE REVOCADA POR CENTRAL.")
                    
                    print(" Deteniendo suministro en curso...")
                    send_to_engine("FORCE_STOP")
                    with status_lock: last_reported_status = "PARADO"
                    # -----------------------------------------------

                    print("Cerrando sesión localmente...")
                    auth_token = None
                    encryption_key = None
                    delete_credentials(cp_id)
                    sock.close()
                    with central_lock: central_conn = None
                    return # Matamos el hilo

    except Exception as e:
        print(f"\n [HILO] Conexión perdida con Central: {e}")
    finally:
        with central_lock:
            if central_conn == sock:
                central_conn = None
        print("\nℹ Desconectado de Central. (Pulsa Enter para refrescar menú)")

def main():
    global central_conn, cp_id_global, last_reported_status, auth_token, encryption_key
    
    parser = argparse.ArgumentParser()
    parser.add_argument('--central-ip', required=True)
    parser.add_argument('--central-port', type=int, required=True)
    parser.add_argument('--engine-ip', required=True)
    parser.add_argument('--engine-port', type=int, required=True)
    parser.add_argument('--cp-id', required=True)
    parser.add_argument('--registry-ip', required=True)
    parser.add_argument('--registry-port', type=int, default=6000)
    args = parser.parse_args()
    
    cp_id_global = args.cp_id
    
    # Cargar credenciales si existen
    auth_token, encryption_key = load_credentials(args.cp_id)

    UBICACIONES_MAP = {
        "MAD": "Madrid, Gran Vía", "ALC": "Alicante, Puerto", "BCN": "Barcelona, Diagonal",
        "VAL": "Valencia, Centro", "SEV": "Sevilla, Torre del Oro", "BIL": "Bilbao, Guggenheim",
        "ZAZ": "Zaragoza, El Pilar", "CRT": "Cartagena, Puerto", "GUA": "Guadalajara, Centro",
        "UBT": "Ulan Bator, Ciudad de Vacaciones"
    }
    prefix = args.cp_id[:3].upper()
    location = UBICACIONES_MAP.get(prefix, f"Ciudad Desconocida ({args.cp_id})")
    price = round(random.uniform(0.30, 0.85), 2)

    # Iniciar Health Check
    threading.Thread(target=health_check_loop, args=(args.engine_ip, args.engine_port, args.cp_id), daemon=True).start()

    print(f"\n MONITOR DE PUNTO DE CARGA: {args.cp_id} ")
    print(f" Ubicación: {location} | {price} €/kWh")
    
    while True:
        conn_state = "CONECTADO " if central_conn else "DESCONECTADO "
        reg_state = "REGISTRADO " if auth_token else "NO REGISTRADO "
        
        print(f"\n--- ESTADO: {reg_state} | {conn_state} ---")
        print("1. ALTA en Registry")
        print("2. BAJA en Registry")
        if not central_conn:
            print("3. CONECTAR a Central")
        else:
            print("3. DESCONECTAR de Central")
        print("4. Salir")
        
        op = input("Selecciona opción: ")

        if op == "1":
            if auth_token:
                print(" Ya estás registrado.")
                continue
                
            payload = {"cp_id": args.cp_id, "location": location, "price": price}
            res = api_registry_request("POST", args.registry_ip, args.registry_port, "cp", payload)
            
            if res and res.status_code == 200:
                data = res.json()
                auth_token = data['auth_token']
                encryption_key = data['encryption_key']
                save_credentials(args.cp_id, auth_token, encryption_key)
                print(f" REGISTRO OK.")
            else:
                print(" Error en registro.")

        elif op == "2":
            res = api_registry_request("DELETE", args.registry_ip, args.registry_port, f"cp/{args.cp_id}", None)
            
            if res and res.status_code == 200:
                print(" CP dado de baja correctamente.")
                auth_token = None
                encryption_key = None
                delete_credentials(args.cp_id)
                
                # Si estábamos conectados, desconectamos
                if central_conn:
                    try:
                        central_conn.close()
                    except: pass
                    with central_lock: central_conn = None
            else:
                print(" Error al dar de baja (Registry devolvió error).")

        elif op == "3":
            if central_conn:
                # Si ya estamos conectados, la opción 3 es para DESCONECTAR
                print(" Desconectando voluntariamente...")
                try: central_conn.close()
                except: pass
                with central_lock: central_conn = None
                continue

            # Si no estamos conectados, conectamos
            if not auth_token:
                print(" AVISO: No tienes Token. Regístrate primero (Opción 1).")
                continue
            
            print(f" Conectando a Central...")
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect((args.central_ip, args.central_port))
                s.sendall(f"REGISTER;{args.cp_id};{location};{price}\n".encode())
                
                ack = s.recv(1024).decode()
                if "ACK" in ack:
                    with central_lock: central_conn = s
                    print(" Conexión establecida. Volviendo al menú...")
                    
                    # LANZAMOS EL HILO DE ESCUCHA (NO BLOQUEANTE)
                    t = threading.Thread(target=central_listener_thread, args=(s, args.cp_id), daemon=True)
                    t.start()
                else:
                    print(f" Central no envió ACK: {ack}")
                    s.close()

            except Exception as e:
                print(f" No se pudo conectar: {e}")

        elif op == "4":
            print(" Saliendo...")
            sys.exit(0)

if __name__ == "__main__":
    main()