#!/usr/bin/env python3

import os
import time
import json
import shutil
import argparse
import threading
import subprocess
import contextlib
import io
from collections import defaultdict
import platform
import obsws_python as obs
import pywinctl

# === Example for -T and -S ===
# -T '{"/mnt/AufnahmeSpeicher": "/home/smb/AufnahmeSpeicher/"}'
# -S '{"OBS-move-rec-python3-Konsole": "OBSmovRec-Konsole"}'
# =============================


def get_config_dir(app_name):
    config_dir = None

    if platform.system() == "Windows":
        # Windows
        appdata = os.getenv("APPDATA")
        if appdata:
            config_dir = os.path.join(appdata, app_name)
    elif platform.system() == "Darwin":  # macOS
        config_dir = os.path.join(
            os.path.expanduser("~"), "Library", "Application Support", app_name
        )

    if not config_dir:  # Linux and others
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

    parser.add_argument(
        "-H",
        "--host",
        default=config_defaults.get("host", "localhost"),
        help="OBS WebSocket IP (default localhost)",
    )
    parser.add_argument(
        "-P",
        "--port",
        type=int,
        default=config_defaults.get("port", 4455),
        help="OBS WebSocket PORT (default 4455)",
    )
    parser.add_argument(
        "-p",
        "--password",
        default=config_defaults.get("password", ""),
        help="OBS WebSocket password",
    )
    parser.add_argument(
        "-d",
        "--dest_base",
        default=config_defaults.get("dest_base", ".."),
        help='Where to relocate the Videos from (default "..")',
    )
    parser.add_argument(
        "-t",
        "--track_interval",
        type=int,
        default=config_defaults.get("track_interval", 1),
        help="Window tracking intervall (default 1)",
    )
    parser.add_argument(
        "-c",
        "--track_command",
        default=config_defaults.get("track_command", ""),
        help="Set a window tracking command, so wayland users can still use window tracking",
    )
    parser.add_argument(
        "-T",
        "--translate",
        type=str,
        default=config_defaults.get("translate", {}),
        help="Path translation JSON string",
    )
    parser.add_argument(
        "-S",
        "--shorthand",
        type=str,
        default=config_defaults.get("shorthand", {}),
        help="Shorthand mapping JSON string",
    )
    parser.add_argument(
        "-C",
        "--check_track",
        action="store_true",
        help="Only Check what window would be tracked and the sanitized title",
    )

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


def extract_relevant_title(title: str) -> str:
    # Normalize separators
    title = title.replace("—", "-").strip()

    # Split by dash and clean parts
    parts = [p.strip() for p in title.split("-") if p.strip()]

    # If nothing to process, return as-is
    if parts:
        # Words to ignore
        irrelevant_keywords = [
            "vulkan",
            "direct3d",
            "opengl",
            "metal",
            "dx12",
            "dx11",
            "dx9",
        ]

        # Pop last entriey if irrelevant (case-insensitive)
        last = parts[-1].lower().replace(" ", "")
        for kw in irrelevant_keywords:
            if kw in last and len(last) < len(kw) + 6:
                parts.pop()

    if parts:
        # Take the last list item (usually main title)
        chosen = parts[-1].strip()

        # If it's a path, use the last folder
        if "/" in chosen or "\\" in chosen:
            chosen = os.path.basename(chosen.rstrip("/\\")) or chosen
    else:
        chosen = title

    # Final cleanup: remove stray punctuation and collapse spaces
    chosen = "".join(ch for ch in chosen if ch.isalnum() or ch in " _-").strip()
    chosen = "-".join(chosen.split())

    return chosen


def window_tracker():
    if not CHECK_TRACK:
        print("[INFO] Window tracking started.\n")
    else:
        wait_print_cyle = 5
        print(
            f"[INFO] Printing titles and times for track check with min print intervall {wait_print_cyle}s.\n"
        )

    last_title = None
    except_msg = None
    last_except = None
    print_cyle = {}

    last_time = time.time()

    while not stop_focus_thread:
        if TRACK_COMMAND:
            result = subprocess.run(
                TRACK_COMMAND, shell=True, capture_output=True, text=True
            )
            if result.returncode == 0:
                current_title = result.stdout or "Desktop"
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

        if CHECK_TRACK:
            if last_title.strip() and window_focus_times.get(last_title):
                time_last = window_focus_times.get(last_title)
                if print_cyle.get(last_title, 1) < time_last:
                    print_cyle[last_title] = wait_print_cyle + print_cyle.get(
                        last_title, 1
                    )
                    print("[INFO] Raw Title: " + last_title.strip())
                    sanitize(last_title)
                    print("[INFO] Active for secs: " + str(round(time_last, 2)))
                    print()

    # Final update
    if last_title:
        window_focus_times[last_title] += time.time() - last_time

    print("[INFO] Window tracking stopped.")


def sanitize(title: str) -> str:
    cleaned = extract_relevant_title(title)

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
            print(f"[INFO] Found Translatiton for: '{path}'")
            print(f"[INFO] Translating to: '{p}'")
            return p

    return path


def move_recording(path, window_title):
    path = path_translate(path)

    if not path or not os.path.exists(path):
        print(f"[ERROR] Recording file not found: '{path}'")
        return

    sanitized_title = sanitize(window_title)
    target_dir = os.path.abspath(
        os.path.join(os.path.dirname(path), DESTINATION_BASE, sanitized_title)
    )
    os.makedirs(target_dir, exist_ok=True)

    filename = os.path.basename(path)
    dest_path = os.path.join(target_dir, filename)

    try:
        shutil.move(path, dest_path)
        print(f"[INFO] Recording moved to: '{dest_path}'")
    except Exception as e:
        print(f"[ERROR] Failed to move recording: {e}")


def add_files(path):
    global latest_output_paths, last_output_paths
    if (
        path
        and isinstance(path, str)
        and path not in latest_output_paths
        and path not in last_output_paths
    ):
        latest_output_paths.append(path)
        print(f"[INFO] OBS Recording file path added: '{path}'")


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
    if output_active == last_output_active or p in [
        "OBS_WEBSOCKET_OUTPUT_RESUMED",
        "OBS_WEBSOCKET_OUTPUT_PAUSED",
    ]:
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
    global OBS_HOST, OBS_PORT, OBS_PASSWORD, DESTINATION_BASE
    global TRACK_INTERVAL, TRACK_COMMAND, CHECK_TRACK
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
    #  config_defaults["successful_sockets"][host_string] = int(time.time())
    save_config(
        vars(args)
        | {"successful_sockets": config_defaults.setdefault("successful_sockets", {})}
    )

    OBS_HOST = args.host
    OBS_PORT = args.port
    OBS_PASSWORD = args.password
    DESTINATION_BASE = args.dest_base
    TRACK_INTERVAL = args.track_interval
    TRACK_COMMAND = args.track_command
    CHECK_TRACK = args.check_track

    if CHECK_TRACK:
        stop_focus_thread = False
        print("\n[INFO] Running in Tracking Only Mode.")
        print("[INFO] Close with Ctrl+c\n")
        try:
            window_tracker()
        except KeyboardInterrupt:
            print("\n[Info] Stopping Tracking")
        except Exception as e:
            print(f"\n[Warn] Window Tracking error: {e}")
        finally:
            print("\n[INFO] Exiting (Tracking Only Mode).")
        return

    print("[INFO] Connecting to OBS WebSocket...")

    successful_sockets = config_defaults.setdefault("successful_sockets", {})
    host_string = f"{OBS_HOST}:{OBS_PORT}"
    last_success = successful_sockets.setdefault(host_string, None)
    cl_evt = None
    wait_message_printed = False

    while True:
        try:
            # suppress any traceback printed to stderr
            with contextlib.redirect_stderr(io.StringIO()):
                cl_evt = obs.EventClient(
                    host=OBS_HOST, port=OBS_PORT, password=OBS_PASSWORD
                )

            cl_evt.callback.register(on_record_file_changed)
            cl_evt.callback.register(on_record_state_changed)

            print("[INFO] Connected to OBS.")

            # Save successful connection info
            config_defaults["successful_sockets"][host_string] = int(time.time())
            save_config(config_defaults)
            break

        except Exception:
            if last_success:
                if not wait_message_printed:
                    print(
                        "[INFO] OBS not running, waiting for OBS to become available..."
                    )
                    wait_message_printed = True
                time.sleep(2)
                continue
            else:
                print("[ERROR] Failed to connect to OBS WebSocket.")
                cl_evt = None
                break

    # Now listen normally
    try:
        print("[INFO] Listening to events...")
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[INFO] Exiting.")
    finally:
        stop_focus_thread = True
        if cl_evt:
            try:
                cl_evt.disconnect()
            except Exception:
                pass


if __name__ == "__main__":
    main()
