"""
Módulo central_gui.py (VERSIÓN FINAL RELEASE 2)
Contiene la clase CentralApp con la nueva Tabla de Auditoría.
"""

import tkinter as tk
from tkinter import ttk, font
import queue 
from datetime import datetime

# --- COLORES Y ESTILOS ---
COLOR_VERDE = "#4CAF50"      # Activado / Suministrando
COLOR_NARANJA = "#FF9800"    # Parado (Out of Order)
COLOR_ROJO = "#F44336"       # Averiado
COLOR_ROJO_OSCURO = "#B71C1C" # Revocar Clave
COLOR_GRIS = "#9E9E9E"       # Desconectado
COLOR_FONDO_REJILLA = "#333333" 
COLOR_TEXTO = "#FFFFFF"        
COLOR_FONDO_APP = "#212121"

class CPPanel(tk.Frame):
    """Panel individual que representa un Punto de Carga en la rejilla."""
    def __init__(self, parent, cp_id, location, price):
        super().__init__(parent, borderwidth=2, relief="solid", bg=COLOR_GRIS, padx=5, pady=5)
        self.cp_id = cp_id
        
        # ID del CP
        self.lbl_id = tk.Label(self, text=cp_id, bg=COLOR_GRIS, fg=COLOR_TEXTO, font=("Arial", 12, "bold"))
        self.lbl_id.pack(fill="x")
        
        # Ubicación
        self.lbl_location = tk.Label(self, text=location, bg=COLOR_GRIS, fg=COLOR_TEXTO, font=("Arial", 9))
        self.lbl_location.pack(fill="x")
        
        # Estado
        self.lbl_status = tk.Label(self, text="DESCONECTADO", bg=COLOR_GRIS, fg=COLOR_TEXTO, font=("Arial", 10, "bold"))
        self.lbl_status.pack(fill="x", pady=2)
        
        # Info extra
        self.lbl_info = tk.Label(self, text="--", bg=COLOR_GRIS, fg=COLOR_TEXTO, font=("Arial", 9))
        self.lbl_info.pack(fill="x")

        # Precio
        self.lbl_price = tk.Label(self, text=price, bg=COLOR_GRIS, fg=COLOR_TEXTO, font=("Arial", 8))
        self.lbl_price.pack(side="bottom")

    def update_state(self, new_state, data=None):
        color = COLOR_GRIS
        text_status = new_state
        
        if new_state == "ACTIVADO":
            color = COLOR_VERDE
        elif new_state == "SUMINISTRANDO":
            color = COLOR_VERDE
            if data:
                text_status = f"CARGANDO\n{data.get('driver','')}"
                self.lbl_info.config(text=f"{data.get('kwh','0')} kWh | {data.get('eur','0')}€")
        elif new_state == "PARADO":
            color = COLOR_NARANJA
            text_status = "Out of Order"
            if data and 'temp' in data:
                self.lbl_info.config(text=f"❄️ {data['temp']}ºC ❄️")
        elif new_state == "AVERIADO":
            color = COLOR_ROJO
        elif new_state == "ESPERANDO_INICIO":
            color = COLOR_VERDE
            text_status = "RESERVADO"

        # Actualizar colores
        self.config(bg=color)
        for widget in self.winfo_children():
            widget.config(bg=color)
            
        self.lbl_status.config(text=text_status)
        if new_state not in ("SUMINISTRANDO", "PARADO"):
            self.lbl_info.config(text="--")


class CentralApp(tk.Tk):
    """Ventana principal de la Central."""
    
    def __init__(self, gui_queue):
        super().__init__()
        self.title("EV Central Management System (Release 2)")
        self.geometry("1100x750")
        self.configure(bg=COLOR_FONDO_APP)
        
        self.gui_queue = gui_queue
        self.backend_controller = None
        self.cp_widgets = {} 
        
        self._create_widgets()
        self.after(100, self._process_queue)

    def set_controller(self, controller):
        self.backend_controller = controller

    def load_initial_cps(self, cps_list):
        for cp in cps_list:
            self._add_new_cp(cp)

    def _add_new_cp(self, cp_data):
        cp_id = cp_data['id']
        if cp_id in self.cp_widgets: return

        # Calcular fila/columna dinámica
        idx = len(self.cp_widgets)
        row = idx // 4 
        col = idx % 4
        
        panel = CPPanel(self.scrollable_frame, cp_id, cp_data['loc'], cp_data['price'])
        panel.grid(row=row, column=col, padx=10, pady=10)
        self.cp_widgets[cp_id] = panel
        self.scrollable_frame.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _create_widgets(self):
        # 1. CABECERA
        header = tk.Label(self, text="⚡ EVCharging Network Control Center ⚡", 
                         bg="#0D47A1", fg="white", font=("Segoe UI", 16, "bold"), pady=10)
        header.pack(fill="x")

        # 2. ÁREA PRINCIPAL (Izquierda: Rejilla, Derecha: Logs/Admin)
        main_frame = tk.PanedWindow(self, orient="horizontal", bg=COLOR_FONDO_APP)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)
        
        # --- IZQUIERDA: REJILLA DE CPs ---
        left_frame = tk.LabelFrame(main_frame, text=" Estado de Puntos de Carga ", 
                                  bg=COLOR_FONDO_REJILLA, fg="white", font=("Arial", 10, "bold"))
        main_frame.add(left_frame, width=700)
        
        self.canvas = tk.Canvas(left_frame, bg=COLOR_FONDO_REJILLA)
        scrollbar = ttk.Scrollbar(left_frame, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg=COLOR_FONDO_REJILLA)
        
        self.scrollable_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # --- DERECHA: LOGS Y ADMIN ---
        right_panel = tk.Frame(main_frame, bg=COLOR_FONDO_APP)
        main_frame.add(right_panel)

        # A) REGISTRO DE SOLICITUDES (Cargas)
        lbl_req = tk.Label(right_panel, text="Peticiones de Carga", bg=COLOR_FONDO_APP, fg="white", font=("Arial", 9, "bold"))
        lbl_req.pack(pady=(0,5))
        
        self.tree_req = ttk.Treeview(right_panel, columns=("Time", "User", "CP"), show="headings", height=6)
        self.tree_req.heading("Time", text="Hora")
        self.tree_req.heading("User", text="Usuario")
        self.tree_req.heading("CP", text="CP")
        self.tree_req.column("Time", width=70)
        self.tree_req.column("User", width=80)
        self.tree_req.column("CP", width=70)
        self.tree_req.pack(fill="x", pady=(0, 15))

        # B) AUDITORÍA DE SEGURIDAD (REQUISITO RELEASE 2)
        lbl_audit = tk.Label(right_panel, text="AUDITORÍA DE SEGURIDAD  ", bg=COLOR_FONDO_APP, fg="yellow", font=("Arial", 9, "bold"))
        lbl_audit.pack(pady=(0,5))
        
        # Estilo para tabla auditoría
        style = ttk.Style()
        style.configure("Audit.Treeview", background="#222", foreground="white", fieldbackground="#222")
        
        self.tree_audit = ttk.Treeview(right_panel, columns=("Time", "IP", "Action", "Desc"), show="headings", height=10, style="Audit.Treeview")
        self.tree_audit.heading("Time", text="Fecha/Hora")
        self.tree_audit.heading("IP", text="Origen (IP)")
        self.tree_audit.heading("Action", text="Acción")
        self.tree_audit.heading("Desc", text="Detalle")
        
        self.tree_audit.column("Time", width=120)
        self.tree_audit.column("IP", width=100)
        self.tree_audit.column("Action", width=120)
        self.tree_audit.column("Desc", width=200)
        
        # Scrollbar para auditoría
        audit_scroll = ttk.Scrollbar(right_panel, orient="vertical", command=self.tree_audit.yview)
        self.tree_audit.configure(yscrollcommand=audit_scroll.set)
        
        self.tree_audit.pack(side="top", fill="both", expand=True)
        # audit_scroll.pack(side="right", fill="y") # (Opcional si se quiere scroll visible)

        # C) ADMIN
        frame_admin = tk.LabelFrame(right_panel, text=" Gestión Manual ", bg="#2c3e50", fg="white", pady=5, padx=5)
        frame_admin.pack(fill="x", side="bottom", pady=10)

        self.admin_cp_entry = tk.Entry(frame_admin)
        self.admin_cp_entry.pack(fill="x", pady=5)
        tk.Button(frame_admin, text="PARAR / REANUDAR", bg="#2196F3", fg="white", command=self._on_toggle_cp).pack(fill="x")
        tk.Button(frame_admin, text="☠️ REVOCAR CLAVE", bg=COLOR_ROJO_OSCURO, fg="white", command=self._on_revocar).pack(fill="x", pady=5)

    # --- LOGICA ---
    def _on_toggle_cp(self):
        cp_id = self.admin_cp_entry.get().strip().upper()
        if not cp_id or not self.backend_controller: return
        self.backend_controller.request_parar_cp(cp_id) # Simplificado

    def _on_revocar(self):
        cp_id = self.admin_cp_entry.get().strip().upper()
        if cp_id and self.backend_controller:
            self.backend_controller.request_revocar_clave(cp_id)

    def _process_queue(self):
        try:
            while True:
                if not hasattr(self, 'queue'): return
                message = self.queue.get_nowait()
                msg_type = message[0]
                
                if msg_type == "UPDATE_CP":
                    _, cp_id, state, data = message
                    self._update_cp_state(cp_id, state, data)
                elif msg_type == "ADD_REQUEST":
                    _, date, start, user, cp = message
                    self.tree_req.insert("", 0, values=(start, user, cp))
                elif msg_type == "ADD_AUDIT":
                    # Requisito 5: Mostrar auditoría estructurada
                    _, date, ip, action, desc = message
                    self.tree_audit.insert("", 0, values=(date, ip, action, desc))
                elif msg_type == "NEW_CP":
                    _, cp_data = message
                    self._add_new_cp(cp_data)
                elif msg_type == "ADD_MESSAGE":
                    # Mensajes genéricos van también a auditoría como "INFO"
                    _, txt = message
                    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    self.tree_audit.insert("", 0, values=(now, "SISTEMA", "INFO", txt))
                    
        except queue.Empty:
            pass
        finally:
            self.after(100, self._process_queue)

    def _update_cp_state(self, cp_id, state, data):
        if cp_id in self.cp_widgets:
            self.cp_widgets[cp_id].update_state(state, data)

if __name__ == "__main__":
    q = queue.Queue()
    app = CentralApp(q)
    app.mainloop()