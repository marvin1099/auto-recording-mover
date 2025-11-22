# Auto Recording Mover

Automatically moves video recordings from OBS Studio into uniquely named folders based on the current active window title.  
Supports path translation (e.g., from mounted network shares), shorthand mappings to clean up folder names,  
and custom window tracking commands (useful on Wayland).  
#### This script only works with the OBS WebSocket and therefore requires OBS Studio.

## Requirements

This script requires Python 3.8+ and the following Python packages:

- [`obsws-python`](https://pypi.org/project/obsws-python/)
- [`pywinctl`](https://pypi.org/project/pywinctl/)

## Download

You can get the latest release from:
- **Main Repository:** [Codeberg Releases](https://codeberg.org/marvin1099/auto-recording-mover/releases)
- **Backup Mirror:** [GitHub Releases](https://github.com/marvin1099/auto-recording-mover/releases)

## Installation (with virtual environment)

On Windows, venv creation can be skipped (pip install still needed):

```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install obsws-python pywinctl
````

Or use the helper scripts:
Running them will make the venv for you and run `OBS-recording-mover.py` with the arguments of the helper:

```bash
./OBS-recording-mover.sh  # On Windows: OBS-recording-mover.bat
```

## Usage

```bash
python OBS-recording-mover.py [options]
```

Once run for the first time, all provided or default arguments are saved to a config file in your OS-native config directory.  
Check [Configuration](#configuration) for config file location.
These values are reused next time unless overridden on the command line (-C is excluded).

Keep the script running while recording (or all the time).  
After the recording is finished, the output file will be moved to:

```
$video_dir/$dest_base/$WindowName
```

### Command-Line Arguments

| Option                   | Description                                                               |
| ------------------------ | ------------------------------------------------------------------------- |
| `-h`, `--help`           | Show help (usage) message and exit                                        |
| `-H`, `--host`           | OBS WebSocket IP (default: `localhost`)                                   |
| `-P`, `--port`           | OBS WebSocket port (default: `4455`)                                      |
| `-p`, `--password`       | OBS WebSocket password                                                    |
| `-d`, `--dest_base`      | Base destination directory (default: `..`; means parent folder)           |
| `-t`, `--track_interval` | Window tracking interval in seconds (default: `1`)                        |
| `-c`, `--track_command`  | Custom command for window tracking (useful on Wayland if pywinctl fails)  |
| `-T`, `--translate`      | JSON string for path translation (e.g. `{"X:/record": "/mnt/share"}`)     |
| `-S`, `--shorthand`      | JSON string to map long window titles to short names                      |
| `-C`, `--check_track`    | Only check what window would be tracked and the sanitized title           |
### Example

```bash
python OBS-recording-mover.py -p REALOBSPASS \
  -T '{"/mnt/drive/records":"/mnt/smb-share/records"}' \
  -S '{"Long-Random-Window-Title":"RandomVids"}' \
  -c 'kdotool getwindowname $(kdotool getactivewindow)'
```

It's to note that the **sanitized titles** are expected as shorthand keys.  
You can get the sanitized titles by running the script with -C (tracking only mode, obs not used here),  
while the script is running, focus the window that you want to get.  
The CLI output will show the sanitized titles.

## Configuration

The configuration is saved after first run and auto-loaded in future runs.  
You can also manually edit the config file if needed:

* **Linux:** `~/.config/OBS-recording-mover/mover_config.json`
* **Windows:** `%APPDATA%\OBS-recording-mover\mover_config.json`
* **macOS:** `~/Library/Application Support/OBS-recording-mover/mover_config.json`

## Notes

* On Linux, `pywinctl` requires an active window manager with X11 api (otherwise check the note below).
* On Wayland, `--track_command` can be used to provide your own active window name retrieval command.
  For example, on KDE with `kdotool`:
  ```bash
  kdotool getwindowname $(kdotool getactivewindow)
  ```
  On wlroots with `wlrctl` (not entirely sure this works, I don't use wlroots):
  ```
  timeout 1 wlrctl find state:active | grep "title:" | sed -e 's/^.*title://'
  ```
  Other compositors may have similar tools.

* OBS must have the [OBS WebSocket](https://github.com/obsproject/obs-websocket) server enabled.
