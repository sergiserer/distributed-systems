import tkinter as tk
from tkinter import ttk, messagebox
import requests
import os

# --- CONFIGURACIÓN ---
# Apuntamos a la Central en el PC 1
CENTRAL_API_URL = "http://192.168.1.24:5000/api/alert/weather"

def load_api_key():
    
    try:
        # Construye la ruta absoluta al archivo api_key.txt
        file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'api_key.txt')
        
        with open(file_path, 'r') as f:
            key = f.read().strip() # Elimina espacios y saltos de línea
            if key:
                print(f" API Key cargada correctamente.")
                return key
            else:
                print(" El archivo api_key.txt está vacío.")
    except FileNotFoundError:
        print(" No se encuentra el archivo 'api_key.txt'.")
    except Exception as e:
        print(f" Error leyendo api_key.txt: {e}")
    
    return "CLAVE_NO_ENCONTRADA"

# Cargamos la clave al iniciar
API_KEY = load_api_key()

class WeatherApp:
    def __init__(self, root):
        self.root = root
        self.root.title("EV_W - Weather Control Office")
        self.root.geometry("500x400")
        
        self.cities = [] 

        # --- PANEL SUPERIOR: Añadir Ciudad ---
        frame_input = tk.Frame(root, pady=10)
        frame_input.pack()
        
        tk.Label(frame_input, text="Añadir Ciudad (Real):").pack(side=tk.LEFT)
        self.entry_city = tk.Entry(frame_input, width=15)
        self.entry_city.pack(side=tk.LEFT, padx=5)
        tk.Button(frame_input, text="Añadir", command=self.add_city).pack(side=tk.LEFT)

        # --- PANEL DE CONTROL: Puerta Trasera (Simulación) ---
        frame_sim = tk.Frame(root, pady=5, bg="#ffdddd")
        frame_sim.pack(fill="x", padx=10)
        
        self.force_cold_var = tk.BooleanVar(value=False)
        chk_cold = tk.Checkbutton(frame_sim, text="[TEST] FORZAR SIMULACIÓN FRÍO (-5ºC)", 
                                  variable=self.force_cold_var, bg="#ffdddd", fg="red", font=("Arial", 10, "bold"))
        chk_cold.pack()

        # --- TABLA DE ESTADOS ---
        self.tree = ttk.Treeview(root, columns=("City", "Temp", "Status"), show="headings", height=10)
        self.tree.heading("City", text="Ciudad")
        self.tree.heading("Temp", text="Temp (ºC)")
        self.tree.heading("Status", text="Estado Enviado")
        self.tree.column("City", width=150)
        self.tree.column("Temp", width=100)
        self.tree.column("Status", width=150)
        self.tree.pack(padx=10, pady=10, fill="both", expand=True)

        # --- BARRA DE ESTADO ---
        self.lbl_log = tk.Label(root, text="Iniciando sistema...", fg="gray")
        self.lbl_log.pack(side=tk.BOTTOM, pady=5)

        # Iniciar bucle de 4 segundos
        self.check_weather_loop()

    def add_city(self):
        city = self.entry_city.get().strip()
        if city and city not in self.cities:
            self.cities.append(city)
            self.tree.insert("", "end", iid=city, values=(city, "...", "Pendiente"))
            self.entry_city.delete(0, tk.END)

    def check_weather_loop(self):
        """Consulta clima real / simula frío si el checkbox está marcado."""
        print("--- Ciclo de comprobación ---")
        
        is_simulation = self.force_cold_var.get()
        
        for city in self.cities:
            temp = None
            
            # 1. Decidir si usamos API real o Simulación
            if is_simulation:
                temp = -5.0  # Valor forzado para test
                source = "SIMULADO"
            else:
                temp = self.get_openweather_temp(city)
                source = "API"

            if temp is not None:
                is_cold = temp < 0
                status_text = "ALERTA ❄️" if is_cold else "OK ☀️"
                
                # Actualizar GUI
                self.tree.item(city, values=(city, f"{temp}ºC ({source})", status_text))
                
                # Notificar a Central
                self.notify_central(city, is_cold, temp)
            else:
                self.tree.item(city, values=(city, "Error API", "No Conexión"))

        # Re-programar para dentro de 4 segundos (4000 ms)
        self.root.after(4000, self.check_weather_loop)

    def get_openweather_temp(self, city):
        try:
            url = f"http://api.openweathermap.org/data/2.5/weather?q={city}&appid={API_KEY}&units=metric"
            res = requests.get(url, timeout=3)
            if res.status_code == 200:
                return res.json()['main']['temp']
        except Exception as e:
            print(f"Error conexión API OpenWeather: {e}")
        return None

    def notify_central(self, city, is_cold, temp_value):
        # AHORA ENVIAMOS TAMBIÉN LA TEMPERATURA ('temp')
        payload = {"location": city, "alert": is_cold, "temp": temp_value}
        try:
            requests.post(CENTRAL_API_URL, json=payload, timeout=2)
            self.lbl_log.config(text=f"Reportado: {city} -> {temp_value}ºC")
        except:
            self.lbl_log.config(text=f"ERROR conectando con Central ({city})", fg="red")

if __name__ == "__main__":
    root = tk.Tk()
    app = WeatherApp(root)
    root.mainloop()