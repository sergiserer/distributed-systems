import os
import sys
import secrets
import requests 
from flask import Flask, request, jsonify

# --- CONFIGURACIÓN ---
REGISTRY_HOST = '0.0.0.0'
REGISTRY_PORT = 6000

# IP de la Central (PC 1)
CENTRAL_IP = "172.20.243.109" 
CENTRAL_API_URL_REG = f"http://{CENTRAL_IP}:5000/api/internal/register"
CENTRAL_API_URL_DEL = f"http://{CENTRAL_IP}:5000/api/internal/delete" # Nuevo endpoint

app = Flask(__name__)

def generate_credentials():
    auth_token = secrets.token_hex(16)      
    encryption_key = secrets.token_urlsafe(32) 
    return auth_token, encryption_key

@app.route('/api/cp', methods=['POST'])
def register_cp():
    data = request.json
    cp_id = data.get('cp_id')
    location = data.get('location')
    price = data.get('price')

    if not cp_id or not location:
        return jsonify({"error": "Faltan datos"}), 400

    print(f"[REGISTRY]  Alta solicitada: {cp_id}")
    token, enc_key = generate_credentials()

    payload_to_central = {
        "cp_id": cp_id,
        "location": location,
        "price": price,
        "encryption_key": enc_key
    }

    try:
        response = requests.post(CENTRAL_API_URL_REG, json=payload_to_central, timeout=5)
        if response.status_code == 200:
            print(f"[REGISTRY]  CP {cp_id} registrado en Central.")
            return jsonify({
                "status": "OK",
                "auth_token": token,
                "encryption_key": enc_key
            }), 200
        else:
            return jsonify({"error": "Central rechazó el registro"}), 500
    except Exception as e:
        print(f"[REGISTRY]  Error conectando a Central: {e}")
        return jsonify({"error": "Central no responde"}), 500

@app.route('/api/cp/<cp_id>', methods=['DELETE'])
def unregister_cp(cp_id):
    
    
    print(f"[REGISTRY]  Solicitud de BAJA para: {cp_id}")
    
    try:
        # Avisamos a Central para que ponga encryption_key = NULL
        payload = {"cp_id": cp_id}
        requests.post(CENTRAL_API_URL_DEL, json=payload, timeout=5)
        
        print(f"[REGISTRY]  Baja procesada para {cp_id}.")
        return jsonify({"status": "OK", "message": "CP dado de baja"}), 200
        
    except Exception as e:
        print(f"[REGISTRY]  Error avisando a Central (Baja forzada): {e}")
        # Devolvemos OK igualmente para que el Monitor se limpie
        return jsonify({"status": "OK", "message": "Baja local forzada"}), 200

if __name__ == "__main__":
    print(f"--- EV_REGISTRY ACTIVO EN {REGISTRY_PORT} ---")
    try:
        app.run(host=REGISTRY_HOST, port=REGISTRY_PORT, debug=False, ssl_context='adhoc')
    except:
        app.run(host=REGISTRY_HOST, port=REGISTRY_PORT, debug=False)