import os
import tkinter as tk
from tkinter import filedialog
import shutil
import subprocess
import sys
import importlib
import time


class PyNesCoreFallback:
    """Fallback core used only when Cython module isn't built."""

    def __init__(self):
        self.cpu_ram = bytearray(2048)
        self.ppu_vram = bytearray(2048)
        self.cycles = 0
        self.rom_data = b""

    def load_cartridge(self, rom_data):
        if not rom_data:
            return False
        self.rom_data = bytes(rom_data)
        self.cycles = 0
        return True

    def execute_frame(self):
        self.cycles += 29780
        return True


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

def _try_build_cython_core():
    pyx_path = os.path.join(SCRIPT_DIR, "nesemucoreac1_0.pyx")
    if not os.path.isfile(pyx_path):
        return False
    try:
        build_cmd = (
            "from setuptools import setup, Extension; "
            "from Cython.Build import cythonize; "
            "ext=[Extension('nesemucoreac1_0', ['nesemucoreac1_0.pyx'], extra_compile_args=['-O3'])]; "
            "setup(script_args=['build_ext','--inplace'], "
            "ext_modules=cythonize(ext, compiler_directives={'language_level':'3'}))"
        )
        subprocess.run(
            [sys.executable, "-c", build_cmd],
            cwd=SCRIPT_DIR,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def resolve_core_backend():
    try:
        mod = importlib.import_module("nesemucoreac1_0")
        return mod.NesEmuCore, "Cython"
    except Exception:
        if not _try_build_cython_core():
            return PyNesCoreFallback, "Python fallback"
        try:
            importlib.invalidate_caches()
            if "nesemucoreac1_0" in sys.modules:
                del sys.modules["nesemucoreac1_0"]
            mod = importlib.import_module("nesemucoreac1_0")
            return mod.NesEmuCore, "Cython (auto-built)"
        except Exception:
            return PyNesCoreFallback, "Python fallback"


nesemucoreac1_0, CORE_BACKEND_LABEL = resolve_core_backend()


class AcNesEmu:
    def __init__(self, root):
        self.root = root
        self.root.title("AC'S NES EMU 0.1")
        self.root.geometry("600x560")
        self.root.resizable(False, False)
        self.root.configure(bg="black")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.core = nesemucoreac1_0()
        self.core_backend = CORE_BACKEND_LABEL
        self.rom_name = "No ROM"
        self.rom_data = b""
        self.is_running = False
        self.is_paused = False
        self.frame_job = None
        self.frame_count = 0
        self.backend_proc = None
        self.target_fps = 60
        self.frame_ms = int(1000 / self.target_fps)
        self.next_frame_time = None

        self.setup_menu()
        self.setup_toolbar()
        self.setup_display()
        self.update_status()

    def setup_menu(self):
        menubar = tk.Menu(self.root)

        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="Open ROM...", command=self.open_rom)
        filemenu.add_command(label="Close ROM", command=self.close_rom)
        filemenu.add_separator()
        filemenu.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="File", menu=filemenu)

        emumenu = tk.Menu(menubar, tearoff=0)
        emumenu.add_command(label="Pause/Resume", command=self.toggle_pause)
        emumenu.add_command(label="Reset", command=self.reset_console)
        menubar.add_cascade(label="Emulation", menu=emumenu)

        configmenu = tk.Menu(menubar, tearoff=0)
        configmenu.add_command(label="Video...")
        configmenu.add_command(label="Sound...")
        configmenu.add_command(label="Input...")
        menubar.add_cascade(label="Config", menu=configmenu)

        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(label="About", command=self.show_about)
        menubar.add_cascade(label="Help", menu=helpmenu)

        self.root.config(menu=menubar)

    def setup_toolbar(self):
        toolbar = tk.Frame(self.root, bg="black", bd=2, relief=tk.RAISED)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        btn_style = {
            "bg": "black",
            "fg": "#00BFFF",
            "activebackground": "#222222",
            "activeforeground": "cyan",
            "font": ("Courier", 10, "bold"),
            "relief": tk.FLAT,
            "padx": 10,
        }

        self.btn_open = tk.Button(toolbar, text="OPEN ROM", command=self.open_rom, **btn_style)
        self.btn_open.pack(side=tk.LEFT, padx=2, pady=2)

        self.btn_pause = tk.Button(toolbar, text="PAUSE", command=self.toggle_pause, **btn_style)
        self.btn_pause.pack(side=tk.LEFT, padx=2, pady=2)

        self.btn_reset = tk.Button(toolbar, text="RESET", command=self.reset_console, **btn_style)
        self.btn_reset.pack(side=tk.LEFT, padx=2, pady=2)

        self.status_var = tk.StringVar()
        status_label = tk.Label(
            toolbar,
            textvariable=self.status_var,
            bg="black",
            fg="#00BFFF",
            font=("Courier", 10, "bold"),
        )
        status_label.pack(side=tk.RIGHT, padx=8)

    def setup_display(self):
        self.canvas = tk.Canvas(self.root, width=512, height=480, bg="black", highlightthickness=0)
        self.canvas.pack(pady=8)
        self.status_title_id = self.canvas.create_text(
            256,
            230,
            text="OPEN A .NES ROM TO START",
            fill="#00BFFF",
            font=("Courier", 16, "bold"),
        )
        self.status_subtitle_id = self.canvas.create_text(
            256,
            260,
            text=f"Core: {self.core_backend}",
            fill="#00BFFF",
            font=("Courier", 12, "bold"),
        )
        self.status_info_id = self.canvas.create_text(
            256,
            285,
            text="",
            fill="#00BFFF",
            font=("Courier", 12, "bold"),
        )

    def set_screen_status(self, line1, line2="", line3=""):
        self.canvas.itemconfig(self.status_title_id, text=line1)
        self.canvas.itemconfig(self.status_subtitle_id, text=line2)
        self.canvas.itemconfig(self.status_info_id, text=line3)

    def parse_ines_header(self, rom_data):
        if len(rom_data) < 16:
            return None
        if rom_data[0:4] != b"NES\x1A":
            return None

        flags6 = rom_data[6]
        flags7 = rom_data[7]
        prg_banks = rom_data[4]
        chr_banks = rom_data[5]
        mapper = ((flags6 >> 4) & 0x0F) | (flags7 & 0xF0)
        has_trainer = bool(flags6 & 0x04)
        mirroring = "vertical" if (flags6 & 0x01) else "horizontal"
        has_battery = bool(flags6 & 0x02)
        nes2 = (flags7 & 0x0C) == 0x08
        header_kind = "NES 2.0" if nes2 else "iNES"

        return {
            "kind": header_kind,
            "mapper": mapper,
            "prg_banks": prg_banks,
            "chr_banks": chr_banks,
            "has_trainer": has_trainer,
            "mirroring": mirroring,
            "has_battery": has_battery,
        }

    def validate_nes_rom(self, rom_data):
        if len(rom_data) < 16:
            return False, "ROM TOO SMALL", None
        info = self.parse_ines_header(rom_data)
        if not info:
            return False, "INVALID NES HEADER", None
        details = (
            f"{info['kind']} | MAP {info['mapper']} | PRG {info['prg_banks']} | "
            f"CHR {info['chr_banks']}"
        )
        return True, details, info

    def open_rom(self):
        filepath = filedialog.askopenfilename(
            title="Select NES ROM",
            filetypes=[
                ("NES ROMs", "*.nes"),
                ("Famicom Disk System", "*.fds"),
                ("Archives", "*.zip *.7z"),
                ("All Files", "*.*"),
            ],
        )
        if not filepath:
            return

        try:
            with open(filepath, "rb") as rom_file:
                rom_data = rom_file.read()
        except OSError as exc:
            self.set_screen_status("LOAD FAILED", str(exc), "")
            return

        ok, details, rom_info = self.validate_nes_rom(rom_data)
        if not ok:
            self.set_screen_status("ROM REJECTED", details, "Expected .nes iNES file")
            return

        # Local Cython core currently supports these mapper IDs.
        local_supported_mappers = {0, 1, 2, 3, 4, 7}
        core_loaded = False
        if rom_info and rom_info["mapper"] in local_supported_mappers:
            core_loaded = self.core.load_cartridge(rom_data)

        self.rom_data = rom_data
        self.rom_name = os.path.basename(filepath)
        self.root.title(f"AC'S NES EMU 0.1 - {self.rom_name}")
        self.is_running = True
        self.is_paused = False
        self.btn_pause.config(text="PAUSE")
        self.frame_count = 0
        self.next_frame_time = time.perf_counter()
        self.update_status()
        self.run_frame()
        self.boot_commercial_rom(filepath, details, core_loaded, rom_info)

    def boot_commercial_rom(self, rom_path, mapper_details, core_loaded, rom_info):
        """Use a proven emulator backend for commercial ROM compatibility."""
        if self.backend_proc and self.backend_proc.poll() is None:
            return

        # Prefer local build if present, then system fceux in PATH.
        local_fceux = "/Volumes/1TB/:STUFF~ /:Coding~/nesemu/fceux-2026/build/src/fceux"
        if os.path.isfile(local_fceux):
            exe = local_fceux
        else:
            exe = shutil.which("fceux")

        mapper_id = rom_info["mapper"] if rom_info else -1
        if not exe:
            if core_loaded:
                self.set_screen_status(
                    "[ CORE RUNNING ]",
                    f"{self.rom_name} | {mapper_details}",
                    f"{self.target_fps} FPS target active",
                )
            else:
                self.set_screen_status(
                    "CORE LOAD FAILED",
                    f"{self.rom_name} | {mapper_details}",
                    f"Install fceux for mapper {mapper_id} compatibility",
                )
            return

        try:
            self.backend_proc = subprocess.Popen([exe, rom_path])
            self.set_screen_status(
                "[ EXTERNAL BACKEND LAUNCHED ]",
                f"{self.rom_name} | {mapper_details}",
                f"Backend: {os.path.basename(exe)} | 60 FPS target active",
            )
        except OSError as exc:
            self.set_screen_status("FCEUX LAUNCH FAILED", str(exc), "")

    def close_rom(self):
        self.stop_frame_loop()
        self.is_running = False
        self.is_paused = False
        self.rom_name = "No ROM"
        self.rom_data = b""
        self.core = nesemucoreac1_0()
        self.core_backend = CORE_BACKEND_LABEL
        self.root.title("AC'S NES EMU 0.1")
        self.set_screen_status("ROM CLOSED", f"Core: {self.core_backend}", "")
        self.btn_pause.config(text="PAUSE")
        self.update_status()
        self.stop_backend()

    def toggle_pause(self):
        if not self.is_running:
            return
        self.is_paused = not self.is_paused
        self.btn_pause.config(text="RESUME" if self.is_paused else "PAUSE")
        self.update_status()

    def reset_console(self):
        if not self.is_running or not self.rom_data:
            return
        self.core = nesemucoreac1_0()
        self.core.load_cartridge(self.rom_data)
        self.frame_count = 0
        self.is_paused = False
        self.btn_pause.config(text="PAUSE")
        self.next_frame_time = time.perf_counter()
        self.update_status()

    def show_about(self):
        self.set_screen_status(
            "AC'S NES EMU 0.1",
            f"Powered by: nesemucoreac1.0 ({self.core_backend})",
            "UI shell + optional fceux commercial backend",
        )

    def run_frame(self):
        if self.frame_job is not None:
            return

        def tick():
            self.frame_job = None
            if not self.is_running:
                return

            if not self.is_paused:
                self.core.execute_frame()
                self.frame_count += 1
                self.set_screen_status(
                    "[ CYTHON CORE RUNNING ]",
                    f"ROM: {self.rom_name}",
                    f"FRAME: {self.frame_count}  CYCLES: {self.core.cycles}",
                )
                self.update_status()

            if self.is_running:
                now = time.perf_counter()
                if self.next_frame_time is None:
                    self.next_frame_time = now
                self.next_frame_time += 1.0 / self.target_fps
                delay_ms = max(1, int((self.next_frame_time - now) * 1000))
                if self.next_frame_time < now:
                    self.next_frame_time = now
                    delay_ms = 1
                self.frame_job = self.root.after(delay_ms, tick)

        self.frame_job = self.root.after(0, tick)

    def stop_frame_loop(self):
        if self.frame_job is not None:
            self.root.after_cancel(self.frame_job)
            self.frame_job = None

    def update_status(self):
        state = "PAUSED" if self.is_paused else ("RUNNING" if self.is_running else "IDLE")
        self.status_var.set(f"{state} | {self.rom_name}")

    def on_close(self):
        self.stop_frame_loop()
        self.stop_backend()
        self.is_running = False
        self.root.destroy()

    def stop_backend(self):
        if self.backend_proc and self.backend_proc.poll() is None:
            try:
                self.backend_proc.terminate()
            except OSError:
                pass
        self.backend_proc = None


if __name__ == "__main__":
    root = tk.Tk()
    app = AcNesEmu(root)
    root.mainloop()
