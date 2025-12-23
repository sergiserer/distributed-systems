# EV Charging Network Simulator

A comprehensive distributed system that simulates the management, operation, and security of an Electric Vehicle (EV) charging network. This project implements a hybrid architecture using Apache Kafka for asynchronous telemetry, TCP Sockets for real-time control, and a REST API for administration.

![Python](https://img.shields.io/badge/Python-3.10-blue) ![Kafka](https://img.shields.io/badge/Apache%20Kafka-7.4-red) ![Flask](https://img.shields.io/badge/Flask-API-green) ![Cryptography](https://img.shields.io/badge/Security-Fernet-yellow)

## System Architecture

The ecosystem consists of several independent microservices and nodes:

1.  **EV Central (Core):** The system's brain. It manages the SQLite database, validates transactions, maintains the global state of charging points, and exposes a REST API and a GUI for administration.
2.  **EV Registry (Auth):** Certification Authority service. It handles the registration of new charging points and issues Auth Tokens and Encryption Keys (Fernet) for secure communication.
3.  **Charging Points (CP):**
    * **Engine:** Simulates hardware, battery charging, and sends encrypted real-time telemetry via Kafka.
    * **Monitor:** Local interface for the charging point, handling maintenance modes and connection heartbeats.
4.  **Weather Service:** Monitors real-time weather conditions (via OpenWeather API). If freezing temperatures (< 0ºC) are detected, it automatically halts affected chargers to prevent hardware damage.
5.  **EV Driver App:** Client application for drivers. It allows requesting manual charges or automating routes using service files.
6.  **Web Dashboard:** A public HTML/JS dashboard consuming the Central API to display network status and real-time audit logs.

## Security Features (Release 2)

This release introduces simulated security measures:

* **Fernet Encryption:** Telemetry data sent via Kafka is encrypted using rotating symmetric keys.
* **Node Authentication:** CPs must register with the EV_Registry to obtain valid credentials before connecting to the Central system.
* **Audit Logging:** Critical actions (connections, status changes, weather alerts) are recorded in system_audit.log and visible in the Central GUI.
* **Remote Revocation:** The Central admin can remotely revoke security keys for compromised CPs, effectively disconnecting them from the network.

## Prerequisites

* Python 3.10+
* Docker & Docker Compose (Required for the Kafka Broker)
* Internet Connection (For OpenWeather API)

## Installation

1.  **Clone the repository:**

    ```bash
    git clone https://github.com/sergiserer/distributed-systems.git
    cd ev-charging-network
    ```

2.  **Install dependencies:**
    It is recommended to use a virtual environment.

    ```bash
    pip install -r requirements.txt
    ```

## Configuration (Important)

* **IP Addresses:** The system is currently configured with static IPs (e.g., 192.168.1.24). You must update `launch.py`, `index.html`, and `EV_W.py` to match your local network IP or use localhost.
* **Weather API:** Create a file named `api_key.txt` in the root directory and paste your OpenWeatherMap API key inside.

## Execution Guide

The startup order is critical for the distributed system to function correctly.

### 1. Start Infrastructure
Launch the Zookeeper and Kafka containers:

```bash
docker-compose -f docker-compose-central.yml up -d
```

### 2. Start Core Services
Open separate terminals for each service:

Central (Main Server & GUI):

```bash
python EV_Central.py
```

Registry (Auth Service):

```bash
python EV_Registry.py
```

### 3. Deploy Charging Points
Use the automated launcher to start multiple CP simulators (Engine + Monitor) simultaneously:

```bash
python launch.py
```

This will spawn multiple windows simulating different locations (Madrid, Barcelona, etc.).

### 4. Client & Auxiliary Services (Optional)
Weather Service:

```bash
python EV_W.py
```

Driver App:

```bash
python EV_Driver.py
```

### 5. Web Dashboard
Open `index.html` in your browser to view the live network status and telemetry.

## Project Structure

```text
.
├── docker-compose-central.yml  # Kafka/Zookeeper Configuration
├── launch.py                   # Launcher script for multiple CPs
├── index.html                  # Public Web Dashboard
├── services.txt                # Automated route file for Drivers
│
├── [Core]
│   ├── EV_Central.py           # Central Backend + Flask API
│   ├── central_gui.py          # Central GUI Module
│   ├── init_db.py              # SQLite Database Initializer
│
├── [Services]
│   ├── EV_Registry.py          # Registration & Auth Service
│   ├── EV_W.py                 # Weather Alert Service
│
├── [Nodes]
│   ├── EV_CP_E.py              # Engine (Hardware Simulator)
│   ├── EV_CP_M.py              # Monitor (CP Controller)
│   ├── EV_Driver.py            # Driver Backend
│   └── driver_gui.py           # Driver GUI
```
