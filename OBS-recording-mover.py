#!/usr/bin/env python3

import re
import os
import time
import json
import shutil
import argparse
import threading
import subprocess
from collections import defaultdict
import platform
import obsws_python as obs
import pywinctl

# === CONFIG ===
# {"/mnt/AufnahmeSpeicher": "/home/smb/AufnahmeSpeicher/"}
# {"OBS-move-rec-python3-Konsole": "OBSmovRec-Konsole"}
# ===============


def get_config_dir(app_name):
    config_dir = None

    if platform.system() == 'Windows':
        # Windows
        appdata = os.getenv('APPDATA')
        if appdata:
            config_dir = os.path.join(appdata, app_name)
    elif platform.system() == 'Darwin':  # macOS
        config_dir = os.path.join(os.path.expanduser("~"), "Library", "Application Support", app_name)

    if not config_dir: # Linux and others
        config_dir = os.path.join(os.path.expanduser("~"), ".config", app_name)

    return config_dir

# App-specific config details
APP_NAME = "OBS-recording-mover"
CONFIG_DIR = get_config_dir(APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "mover_config.json")

# Load config if it exists
def load_config():
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

# Save config to disk
def save_config(config):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=4)

# Setup argument parser with config as default
def setup_arg_parser(config_defaults):
    parser = argparse.ArgumentParser(description="OBS Recording Mover")

    parser.add_argument("-H", "--host", default=config_defaults.get("host", "localhost"), help="OBS WebSocket IP (default localhost)")
    parser.add_argument("-P", "--port", type=int, default=config_defaults.get("port", 4455), help="OBS WebSocket PORT (default 4455)")
    parser.add_argument("-p", "--password", default=config_defaults.get("password", ""), help="OBS WebSocket password")
    parser.add_argument("-d", "--dest_base", default=config_defaults.get("dest_base", ".."), help="Where to relocate the Videos from (default \"..\")")
    parser.add_argument("-t", "--track_interval", type=int, default=config_defaults.get("track_interval", 1), help="Window tracking intervall (default 1)")
    parser.add_argument("-c", "--track_command", default=config_defaults.get("track_command", ""), help="Set a window tracking command, so wayland users can still use window tracking")
    parser.add_argument("-s", "--strip", default=config_defaults.get("strip", "-—"), help="Provide any amount of strip characters to remove towards (def: -—)")
    parser.add_argument("-T", "--translate", type=str, default=config_defaults.get("translate", {}), help="Path translation JSON string")
    parser.add_argument("-S", "--shorthand", type=str, default=config_defaults.get("shorthand", {}), help="Shorthand mapping JSON string")

    args = parser.parse_args()
    return args


# Shared state
recording_active = False
latest_output_path = None
window_focus_times = defaultdict(float)
focus_thread = None
stop_focus_thread = False
last_output_active = None
latest_output_paths = []
last_output_paths = []

def get_focused_window_title():
    win = pywinctl.getActiveWindow()
    return win.title if win else "Desktop"

def window_tracker():
    print("[INFO] Window tracking started.")
    last_title = None
    except_msg = None
    last_except = None

    last_time = time.time()

    while not stop_focus_thread:
        if TRACK_COMMAND:
            result = subprocess.run(TRACK_COMMAND, shell=True, capture_output=True, text=True)
            if result.returncode == 0:
                current_title = result.stdout
            else:
                current_title = "Desktop"
                except_msg = f"Window title command, results in errorcode {result.returncode} with message: {result.stdout} {result.stderr}"
                if except_msg != last_except:
                    print(except_msg)
                last_except = except_msg
        else:
            try:
                current_title = get_focused_window_title()
            except Exception as e:
                current_title = "Desktop"
                except_msg = f"Unable to get window title: {e}"
                if except_msg != last_except:
                    print(except_msg)
                last_except = except_msg

        now = time.time()

        if last_title:
            window_focus_times[last_title] += now - last_time

        last_title = str(current_title)
        last_time = float(now)
        time.sleep(TRACK_INTERVAL)

    # Final update
    if last_title:
        window_focus_times[last_title] += time.time() - last_time

    print("[INFO] Window tracking stopped.")

def sanitize(title: str) -> str:
    if STRIP:
        # Strip up to and including the last char in STRIP
        pattern = f"[{re.escape(STRIP)}]"
        match = re.search(pattern, title[::-1])  # search backwards
        if match:
            # Cut everything up to last STRIP char
            last_pos = len(title) - match.start()
            cleaned = title[last_pos:]
    else:
        cleaned = str(title)

    # Replace unwanted characters with underscore
    cleaned = re.sub(r"[^\w\s-]", "", cleaned)       # keep alphanum, _, space, -
    cleaned = re.sub(r"[\s_-]+", "-", cleaned)     # collapse multiple separators
    cleaned = re.sub(r"^[-_]+|[-_]+$", "", cleaned)  # clean - and _ from the ends

    if cleaned:
        print(f"[INFO] Sanitized Window Title: {cleaned}")
    if SHORT_HANDS.get(cleaned):
        cleaned = SHORT_HANDS.get(cleaned)
        print(f"[INFO] Found Short Hand Title: {cleaned}")
    return cleaned

def path_translate(path: str) -> str:
    norm_path = os.path.normpath(path)
    for src_prefix, dst_prefix in PATH_TRANSLATE.items():
        if norm_path.startswith(src_prefix):
            # Use os.path.join to ensure correct separator
            relative_part = os.path.relpath(norm_path, src_prefix)
            p = os.path.join(dst_prefix, relative_part)
            print(f"[INFO] Found Translatiton for: {path}")
            print(f"[INFO] Translating to: {p}")
            return p

    return path

def move_recording(path, window_title):
    path = path_translate(path)

    if not path or not os.path.exists(path):
        print(f"[ERROR] Recording file not found: {path}")
        return

    sanitized_title = sanitize(window_title)
    target_dir = os.path.abspath(os.path.join(os.path.dirname(path), DESTINATION_BASE, sanitized_title))
    os.makedirs(target_dir, exist_ok=True)

    filename = os.path.basename(path)
    dest_path = os.path.join(target_dir, filename)

    try:
        shutil.move(path, dest_path)
        print(f"[INFO] Recording moved to: {dest_path}")
    except Exception as e:
        print(f"[ERROR] Failed to move recording: {e}")


def add_files(path):
    global latest_output_paths, last_output_paths
    if path and isinstance(path, str) and path not in latest_output_paths and path not in last_output_paths:
        latest_output_paths.append(path)
        print(f"[OBS] Recording file path added: {path}")

# === OBS EVENT HANDLERS ===

def on_record_file_changed(data):
    try:
        add_files(data.new_output_path)
    except Exception as e:
        add_files(data.output_path)

def on_record_state_changed(data):
    global recording_active, focus_thread, stop_focus_thread
    global last_output_active, window_focus_times
    global latest_output_paths, last_output_paths

    output_active = data.output_active
    add_files(data.output_path)

    p = data.output_state
    if output_active == last_output_active or p in ["OBS_WEBSOCKET_OUTPUT_RESUMED", "OBS_WEBSOCKET_OUTPUT_PAUSED"]:
        return  # Avoid triggering on pause/resume

    last_output_active = output_active

    if output_active:
        print("[OBS] Recording started.")
        recording_active = True
        window_focus_times.clear()
        stop_focus_thread = False
        focus_thread = threading.Thread(target=window_tracker, daemon=True)
        focus_thread.start()
    else:
        print("[OBS] Recording stopped.")
        recording_active = False
        stop_focus_thread = True
        if focus_thread:
            focus_thread.join()

        if latest_output_paths:
            if window_focus_times:
                dominant_window = max(window_focus_times.items(), key=lambda x: x[1])[0]
                print(f"[INFO] Dominant window: {dominant_window}")
            else:
                dominant_window = "Unknown"
                print("[WARN] No window activity tracked.")

            for latest_output_path in latest_output_paths:
                move_recording(latest_output_path, dominant_window)

            last_output_paths = list(latest_output_paths)
            latest_output_paths = []
        else:
            print("[WARN] No output path recorded from OBS.")

# === MAIN ===

def main():
    global OBS_HOST, OBS_PORT, OBS_PASSWORD, DESTINATION_BASE, TRACK_INTERVAL, TRACK_COMMAND, STRIP
    global PATH_TRANSLATE, SHORT_HANDS

    config_defaults = load_config()
    args = setup_arg_parser(config_defaults)

    # Load translate map
    if not args.translate:
        args.translate = {}

    try:
        if not isinstance(args.translate, dict):
            args.translate = json.loads(args.translate)
    except Exception as e:
        print(f"[WARN] Could not parse --translate: {e}")
        args.translate = {}

    PATH_TRANSLATE = args.translate

    # Load shorthand map
    if not args.shorthand:
        args.shorthand = {}

    try:
        if not isinstance(args.shorthand, dict):
            args.shorthand = json.loads(args.shorthand)
    except Exception as e:
        print(f"[WARN] Could not parse --shorthand: {e}")
        args.shorthand = {}

    SHORT_HANDS = args.shorthand

    save_config(vars(args))

    OBS_HOST = args.host
    OBS_PORT = args.port
    OBS_PASSWORD = args.password
    STRIP = args.strip
    DESTINATION_BASE = args.dest_base
    TRACK_INTERVAL = args.track_interval
    TRACK_COMMAND = args.track_command


    print("[INFO] Connecting to OBS WebSocket...")
    try:
        cl_evt = obs.EventClient(host=OBS_HOST, port=OBS_PORT, password=OBS_PASSWORD)
    except Exception as e:
        print("[ERROR] Failed to connect to WebSocket")
        print(f"[ERROR] Error report: {e}")
        cl_evt = None

    if cl_evt:
        # Register callback functions (no event name needed — auto-mapped from function names)
        cl_evt.callback.register(on_record_file_changed)
        cl_evt.callback.register(on_record_state_changed)

    try:
        if not cl_evt:
            raise NameError("WebSocket Object missing")

        print("[INFO] Listening to events...")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[INFO] Exiting.")
    except NameError:
        print("\n[INFO] Exiting.")
    finally:
        global stop_focus_thread
        stop_focus_thread = True
        cl_evt.disconnect()

if __name__ == "__main__":
    main()
