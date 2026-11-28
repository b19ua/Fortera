"""Small Windows GUI for the Fortera Tello companion bridge. No terminal interaction required."""
from __future__ import annotations

import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, scrolledtext

APP_HOME = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ForteraTelloBridge"
CONFIG_PATH = APP_HOME / "config.json"
BRIDGE_DIR = Path(__file__).resolve().parent.parent
GATEWAY = BRIDGE_DIR / "gateway.py"
PYTHON = Path(sys.executable).with_name("python.exe")
VIDEO_URL = "http://127.0.0.1:8766/video"
WS_URL = "ws://127.0.0.1:8765"


def load_config():
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    data.setdefault("website_origin", "")
    data.setdefault("pairing_token", secrets.token_urlsafe(32))
    data.setdefault("control_token", secrets.token_urlsafe(32))
    data.setdefault("tello_host", "192.168.10.1")
    return data


def save_config(data):
    APP_HOME.mkdir(parents=True, exist_ok=True)
    temp = CONFIG_PATH.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, CONFIG_PATH)


def normalize_origin(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError("Use the published website address beginning with https://")
    if parsed.path not in ("", "/") or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("Enter only the website origin, without /live, path, query or fragment.")
    if parsed.username or parsed.password:
        raise ValueError("The website address must not contain credentials.")
    return f"https://{parsed.netloc}"


class BridgeManager:
    def __init__(self, root):
        self.root = root
        self.config = load_config()
        self.process = None
        self._closing = False
        root.title("Fortera Tello Bridge")
        root.geometry("820x720")
        root.minsize(720, 620)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        title = tk.Label(root, text="FORTERA  /  TELLO BRIDGE", font=("Segoe UI", 18, "bold"))
        title.pack(anchor="w", padx=20, pady=(18, 2))
        tk.Label(root, text="Local Windows companion • standard DJI Tello • Indoor Demo profile",
                 font=("Segoe UI", 10)).pack(anchor="w", padx=20, pady=(0, 14))

        form = tk.Frame(root)
        form.pack(fill="x", padx=20)
        tk.Label(form, text="Published Fortera website origin", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        self.origin = tk.Entry(form, font=("Segoe UI", 11))
        self.origin.pack(fill="x", pady=(4, 10))
        self.origin.insert(0, self.config.get("website_origin", ""))

        tk.Label(form, text="Pairing token (paste this into Live Drone)", font=("Segoe UI", 10, "bold")).pack(anchor="w")
        token_row = tk.Frame(form)
        token_row.pack(fill="x", pady=(4, 8))
        self.token = tk.Entry(token_row, font=("Consolas", 10))
        self.token.pack(side="left", fill="x", expand=True)
        self.token.insert(0, self.config["pairing_token"])
        tk.Button(token_row, text="Copy token", command=self.copy_token, width=13).pack(side="left", padx=(8, 0))
        tk.Button(token_row, text="New token", command=self.new_token, width=13).pack(side="left", padx=(6, 0))

        tk.Label(form, text="Bridge address:  ws://127.0.0.1:8765     |     Camera:  http://127.0.0.1:8766/video",
                 font=("Consolas", 9)).pack(anchor="w", pady=(2, 12))

        buttons = tk.Frame(root)
        buttons.pack(fill="x", padx=20, pady=(0, 10))
        self.start_button = tk.Button(buttons, text="START BRIDGE", command=self.start_bridge,
                                      bg="#176b48", fg="white", activebackground="#21845b",
                                      font=("Segoe UI", 11, "bold"), height=2, width=18)
        self.start_button.pack(side="left")
        self.stop_button = tk.Button(buttons, text="STOP BRIDGE", command=self.stop_bridge,
                                     state="disabled", height=2, width=15)
        self.stop_button.pack(side="left", padx=8)
        tk.Button(buttons, text="Open Live Drone", command=self.open_live, height=2).pack(side="left", padx=8)
        tk.Button(buttons, text="Check Tello Wi-Fi", command=self.check_wifi, height=2).pack(side="right")

        self.status = tk.Label(root, text="Status: NOT RUNNING", anchor="w",
                               font=("Segoe UI", 10, "bold"), fg="#8a4b08")
        self.status.pack(fill="x", padx=20, pady=(3, 8))

        warning = (
            "BEFORE START: connect this PC to the Tello-XXXX Wi-Fi. Keep Internet on Ethernet or a second Wi-Fi adapter.\n"
            "The Indoor Demo profile is deliberately limited. Camera checks are warning-only, NOT obstacle avoidance.\n"
            "Do not fly through doors or windows. Keep the aircraft in a large clear indoor area and use prop guards."
        )
        tk.Label(root, text=warning, justify="left", anchor="w", wraplength=770,
                 bg="#fff4d6", fg="#633f00", padx=10, pady=9).pack(fill="x", padx=20, pady=(0, 10))

        tk.Label(root, text="Bridge log", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=20)
        self.log = scrolledtext.ScrolledText(root, height=16, wrap="word", font=("Consolas", 9), state="disabled")
        self.log.pack(fill="both", expand=True, padx=20, pady=(4, 16))
        self.write_log("Installer ready. Enter the exact origin shown in your published Fortera browser tab.")
        self.root.after(1000, self.poll_process)

    def write_log(self, line):
        self.log.configure(state="normal")
        self.log.insert("end", line.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def copy_token(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(self.token.get().strip())
        self.root.update()
        self.write_log("Pairing token copied to clipboard.")

    def new_token(self):
        if self.process and self.process.poll() is None:
            messagebox.showwarning("Bridge is running", "Stop the bridge before rotating the pairing token.")
            return
        self.token.delete(0, "end")
        self.token.insert(0, secrets.token_urlsafe(32))
        self.write_log("New pairing token generated. Save and restart the bridge to use it.")

    def saved_settings(self):
        origin = normalize_origin(self.origin.get())
        token = self.token.get().strip()
        if len(token) < 24:
            raise ValueError("Pairing token must contain at least 24 characters.")
        self.config.update(website_origin=origin, pairing_token=token,
                           control_token=self.config.get("control_token") or secrets.token_urlsafe(32),
                           tello_host="192.168.10.1")
        save_config(self.config)
        return origin, token

    def start_bridge(self):
        if self.process and self.process.poll() is None:
            return
        try:
            origin, token = self.saved_settings()
        except Exception as exc:
            messagebox.showerror("Check website address", str(exc))
            return
        if not GATEWAY.exists() or not PYTHON.exists():
            messagebox.showerror("Installation incomplete", "The bridge or private Python runtime is missing. Run INSTALL_FORTERA_TELLO.bat again.")
            return
        env = os.environ.copy()
        env.update({
            "FORTERA_BACKEND": "tello",
            "FORTERA_BIND": "127.0.0.1",
            "FORTERA_PORT": "8765",
            "FORTERA_VIDEO_PORT": "8766",
            "FORTERA_BRIDGE_TOKEN": token,
            "FORTERA_CONTROL_TOKEN": self.config["control_token"],
            "FORTERA_ALLOWED_ORIGINS": origin,
            "FORTERA_ENABLE_HARDWARE": "1",
            "FORTERA_TELLO_PROFILE": "indoor_demo",
            "FORTERA_TELLO_HOST": self.config["tello_host"],
            "FORTERA_TELLO_VIDEO": "1",
            "FORTERA_TELLO_MISSION_PADS": "0",
        })
        try:
            self.process = subprocess.Popen(
                [str(PYTHON), "-u", str(GATEWAY)], cwd=str(BRIDGE_DIR), env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                encoding="utf-8", errors="replace", bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        except Exception as exc:
            self.process = None
            messagebox.showerror("Cannot start bridge", str(exc))
            return
        threading.Thread(target=self.read_output, daemon=True).start()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.configure(text="Status: STARTING — waiting for Tello SDK", fg="#8a4b08")
        self.write_log("Starting Tello backend. The bridge will fail closed if SDK/video telemetry is unavailable.")
        self.write_log("Allowed website origin: " + origin)

    def read_output(self):
        proc = self.process
        if not proc or not proc.stdout:
            return
        for line in proc.stdout:
            self.root.after(0, self.on_output, line.rstrip())

    def on_output(self, line):
        self.write_log(line)
        if "Fortera bridge ready: tello" in line:
            self.status.configure(text="Status: BRIDGE RUNNING — connect from Fortera /live", fg="#176b48")
        if "Traceback" in line or "RuntimeError" in line or "Tello did not enter SDK mode" in line:
            self.status.configure(text="Status: STARTUP ERROR — see log", fg="#a12622")

    def stop_bridge(self):
        proc = self.process
        if not proc or proc.poll() is not None:
            self.process = None
            self.refresh_buttons()
            return
        if not messagebox.askyesno(
            "Stop Fortera bridge",
            "First LAND the drone from Fortera Live Drone and confirm it is on the floor.\n\n"
            "Stopping the software while airborne is not a flight stop. Continue?"
        ):
            return
        try:
            request = urllib.request.Request(
                "http://127.0.0.1:8767/shutdown", data=b"", method="POST",
                headers={"X-Fortera-Control-Token": self.config["control_token"]}
            )
            with urllib.request.urlopen(request, timeout=4) as response:
                self.write_log("Local shutdown requested: HTTP " + str(response.status))
        except Exception as exc:
            messagebox.showerror("Graceful stop failed", "The bridge did not confirm shutdown. Do not assume the aircraft has stopped.\n" + str(exc))
            return
        self.status.configure(text="Status: STOPPING — bridge will close its controller link", fg="#8a4b08")
        self.root.after(500, self.finish_stop)

    def finish_stop(self):
        proc = self.process
        if not proc:
            self.refresh_buttons()
            return
        if proc.poll() is None:
            self.root.after(500, self.finish_stop)
            return
        self.write_log("Bridge process stopped.")
        self.process = None
        self.status.configure(text="Status: NOT RUNNING", fg="#8a4b08")
        self.refresh_buttons()

    def refresh_buttons(self):
        running = bool(self.process and self.process.poll() is None)
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")

    def poll_process(self):
        if self.process and self.process.poll() is not None:
            code = self.process.returncode
            self.write_log("Bridge process exited with code " + str(code))
            self.process = None
            self.status.configure(text="Status: NOT RUNNING", fg="#8a4b08")
            self.refresh_buttons()
        self.root.after(1000, self.poll_process)

    def open_live(self):
        try:
            origin = normalize_origin(self.origin.get())
        except Exception as exc:
            messagebox.showerror("Website origin required", str(exc))
            return
        webbrowser.open(origin + "/live")

    def check_wifi(self):
        if self.process and self.process.poll() is None:
            messagebox.showinfo("Bridge already running", "Use the bridge log and the Fortera Check bridge button while connected.")
            return
        try:
            host = self.config.get("tello_host", "192.168.10.1")
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.settimeout(2.5)
                sock.sendto(b"command", (host, 8889))
                data, _ = sock.recvfrom(1024)
            if data.decode("utf-8", "replace").strip() == "ok":
                messagebox.showinfo("Tello reachable", "The Tello SDK replied OK. You can start the Fortera bridge.")
                self.write_log("Tello Wi-Fi check: SDK replied OK.")
            else:
                messagebox.showwarning("Unexpected Tello reply", repr(data))
        except Exception as exc:
            messagebox.showerror(
                "Tello not reachable",
                "Connect this PC to the Tello-XXXX Wi-Fi, then try again.\n"
                "Keep Internet on Ethernet or another Wi-Fi adapter.\n\n" + str(exc)
            )

    def on_close(self):
        if self.process and self.process.poll() is None:
            self.stop_bridge()
            self.root.after(900, self.check_close_complete)
            return
        self.root.destroy()

    def check_close_complete(self):
        if self.process and self.process.poll() is None:
            self.root.after(500, self.check_close_complete)
        else:
            self.root.destroy()


if __name__ == "__main__":
    if not PYTHON.exists():
        messagebox.showerror("Installation incomplete", "Run INSTALL_FORTERA_TELLO.bat first.")
    else:
        app = tk.Tk()
        BridgeManager(app)
        app.mainloop()
