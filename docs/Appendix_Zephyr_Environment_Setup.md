# Appendix: Zephyr RTOS Development Environment Setup

*Dockerized Toolchain on WSL2 (Windows 11) — Validated on Seeed XIAO ESP32-S3*

Prepared as a precursor environment for the Arduino Uno Q (Qualcomm) Physical AI Challenge project. The Uno Q hardware had not yet arrived at the time of this setup, so the toolchain, container pipeline, and USB passthrough flow were validated end-to-end on a XIAO ESP32-S3 board first.

---

## A.1 Purpose and Scope

Before the Arduino Uno Q board arrived, the goal was to de-risk the entire build-and-flash pipeline using hardware already on hand. The XIAO ESP32-S3 was used as a stand-in target. Because Zephyr abstracts hardware behind its Devicetree layer, the same project structure, Docker image, and VS Code workflow carry over to the Uno Q with only the board target argument changing.

- Host: Windows 11 with WSL2 (Ubuntu) and Docker Desktop
- Editor: Visual Studio Code with the Dev Containers extension
- Test hardware: Seeed Studio XIAO ESP32-S3
- Target hardware (pending arrival): Arduino Uno Q

## A.2 Host Prerequisites

The following were installed on the Windows 11 host before any container work began:

- WSL2 with an Ubuntu distribution, enabled via Windows Features and `wsl --install`
- Docker Desktop, configured to use the WSL2 backend
- Visual Studio Code, with the **Dev Containers** extension (`ms-vscode-remote.remote-containers`)

All project files were kept on the native WSL2 filesystem (under `/home/rahuljeyaraj/...`) rather than under `/mnt/c/`, to avoid the well-known file I/O performance penalty of the 9P filesystem bridge when compiling large C codebases.

## A.3 Project Structure

The workspace was scaffolded with a fixed layout, separating the Docker/toolchain definition from the application source so the container could be rebuilt without touching project code:

```
zephyr-workspace/
├── .devcontainer/
│   └── devcontainer.json
├── Dockerfile
├── docker-compose.yml
└── app/
    ├── CMakeLists.txt
    ├── prj.conf
    └── src/
        └── main.c
```

Created in one shot from the WSL2 terminal:

```bash
mkdir -p zephyr-workspace/.devcontainer zephyr-workspace/app/src && \
touch zephyr-workspace/.devcontainer/devcontainer.json \
      zephyr-workspace/Dockerfile \
      zephyr-workspace/docker-compose.yml \
      zephyr-workspace/app/CMakeLists.txt \
      zephyr-workspace/app/prj.conf \
      zephyr-workspace/app/src/main.c
```

Actual path used on this machine:

```
\\wsl.localhost\Ubuntu\home\rahuljeyaraj\workspace\zephyr-workspace
```

## A.4 Docker Environment Definition

The container is built from the official Zephyr project SDK image, which bundles the cross-compilers, CMake, Python toolchain, and `west` meta-tool needed for embedded builds. Application source is bind-mounted in, so the toolchain layer and the project code remain independent — the container can be destroyed and rebuilt without any risk to the code.

### A.4.1 Dockerfile (final, working version)

The Dockerfile went through one correction during setup (covered in A.7). The working version is below.

```dockerfile
FROM ghcr.io/zephyrproject-rtos/zephyr-build:latest

# Set up working directories
WORKDIR /workspace

# Install extra host tools needed for flashing or debugging
USER root
RUN apt-get update && apt-get install -y \
    usbutils \
    picocom \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Initialize West and pull the Zephyr source code as root
RUN west init /workspace/zephyrproject \
    && cd /workspace/zephyrproject \
    && west update \
    && west zephyr-export

# Environment variables
ENV ZEPHYR_BASE=/workspace/zephyrproject/zephyr
ENV PATH="${PATH}:/workspace/zephyrproject/.local/bin"

WORKDIR /workspace/app
```

### A.4.2 docker-compose.yml

Configures the container for interactive use and shares the host's USB subsystem. A device passthrough entry was added later once a hardware-visibility issue surfaced (A.7).

```yaml
services:
  zephyr-dev:
    build: .
    container_name: zephyr_container
    volumes:
      - ./app:/workspace/app
    privileged: true
    network_mode: "host"
    ipc: "host"
    tty: true
    stdin_open: true
    devices:
      - "/dev/ttyACM0:/dev/ttyACM0"
```

### A.4.3 VS Code Dev Container config

`.devcontainer/devcontainer.json` links VS Code directly to the Compose service, so the editor's IntelliSense, terminal, and extensions all run inside the container rather than on the Windows host.

```json
{
    "name": "Zephyr RTOS Development",
    "dockerComposeFile": "../docker-compose.yml",
    "service": "zephyr-dev",
    "workspaceFolder": "/workspace/app",
    "customizations": {
        "vscode": {
            "extensions": [
                "marus25.cortex-debug",
                "ms-vscode.cpptools",
                "twxs.cmake",
                "ms-vscode.cmake-tools"
            ]
        }
    },
    "overrideCommand": true
}
```

## A.5 Application Code (LED Blink)

Zephyr abstracts hardware pin assignment behind Devicetree aliases rather than hardcoding pin numbers in application code. The XIAO ESP32-S3 board definition already exposes the on-board LED as the `led0` alias, mapped to GPIO 21 (labeled `USER_LED` on the board's pinout). Because the code below references that alias rather than a raw pin number, the identical source compiles unchanged for the Arduino Uno Q later — only the `west build` board target changes.

### A.5.1 app/CMakeLists.txt

```cmake
cmake_minimum_required(VERSION 3.20.0)
find_package(Zephyr REQUIRED HINTS $ENV{ZEPHYR_BASE})
project(xiao_blink)

target_sources(app PRIVATE src/main.c)
```

### A.5.2 app/prj.conf

```ini
CONFIG_GPIO=y
```

### A.5.3 app/src/main.c

```c
#include <zephyr/kernel.h>
#include <zephyr/drivers/gpio.h>

#include <zephyr/logging/log.h>
LOG_MODULE_REGISTER(main, LOG_LEVEL_INF);

/* 1000 msec = 1 sec */
#define SLEEP_TIME_MS   1000

/* The devicetree node identifier for the "led0" alias. */
#define LED0_NODE DT_ALIAS(led0)

static const struct gpio_dt_spec led = GPIO_DT_SPEC_GET(LED0_NODE, gpios);

int main(void)
{
	int ret;

	if (!gpio_is_ready_dt(&led)) {
		LOG_ERR("Error: GPIO device %s is not ready", led.port->name);
		return 0;
	}

	ret = gpio_pin_configure_dt(&led, GPIO_OUTPUT_ACTIVE);
	if (ret < 0) {
		LOG_ERR("Error %d: failed to configure %s pin %d", ret, led.port->name, led.pin);
		return 0;
	}

	LOG_INF("Zephyr Blinky Initialization Complete.");

	while (1) {
		ret = gpio_pin_toggle_dt(&led);
		if (ret < 0) {
			LOG_ERR("Failed to toggle LED pin");
		}
		k_msleep(SLEEP_TIME_MS);
	}
	return 0;
}
```

## A.6 Build, Container Boot, and VS Code Workflow

### A.6.1 Build and start the container

Run from the project root in a WSL2 terminal:

```bash
docker compose up -d --build
```

> **Note:** the base image (`ghcr.io/zephyrproject-rtos/zephyr-build:latest`) is large — the initial pull and extraction took roughly 45–60 minutes on this connection, mostly due to a ~4.6 GB layer plus the `west update` step cloning Zephyr's modules. This is a one-time cost; Docker layer caching means a rebuild after a Dockerfile fix does not re-download the base image.

### A.6.2 Attach VS Code to the running container

1. Install the **Dev Containers** extension (Microsoft) if not already present
2. File → Open Folder, and open the project root from its WSL2 path (`~/workspace/zephyr-workspace`)
3. Click the blue/green remote-window icon in the bottom-left status bar, or press `Ctrl+Shift+P`
4. Select **Dev Containers: Reopen in Container**

Once reloaded, the status bar reads "Dev Container: Zephyr RTOS Development" and the integrated terminal opens directly inside `/workspace/app` in the container.

### A.6.3 Compile

From the integrated terminal inside the container:

```bash
west build -b xiao_esp32s3/esp32s3/procpu .
```

A successful build ends with a memory utilization table and an image-creation confirmation:

```
Generating files from /workspace/app/build/zephyr/zephyr.elf for board: xiao_esp32s3/esp32s3/procpu
esptool v5.2.0
Creating ESP32-S3 image...
Merged 10 ELF sections.
Successfully created ESP32-S3 image.
```

For the Uno Q later, only the board target string changes:

```bash
west build -b arduino_uno_q .
```

### A.6.4 USB passthrough and flash

On the Windows host (Administrator PowerShell):

```powershell
usbipd list
usbipd bind --busid <BUSID>
usbipd attach --wsl --busid <BUSID>
```

Inside the container terminal, verify the serial device is visible, then flash:

```bash
ls /dev/ttyACM*
west flash
```

## A.7 Issues Encountered and Resolutions

This is a chronological log of every error hit while bringing the pipeline up, kept here so the same fixes don't need to be rediscovered when repeating this setup for the Uno Q.

---

### Issue 1 — Unknown user during image build

**Issue Encountered**
```
unable to find user zephyruser: no matching entries in passwd file
(failed on the RUN west init ... step of the Dockerfile)
```

**Cause**
The current `ghcr.io/zephyrproject-rtos/zephyr-build:latest` image no longer ships a default `zephyruser` account. The original Dockerfile draft included a `USER zephyruser` line before the `west init` step, switching to a user that doesn't exist in this image version.

**Resolution**
Removed the `USER zephyruser` line entirely so the `west init`/`update`/`export` steps run as root, consistent with the rest of the Dockerfile. Re-ran `docker compose up -d --build`; because earlier layers were already cached, the rebuild skipped the multi-gigabyte base image download and resumed directly at the `apt-get` step.

---

### Issue 2 — Container can't see the C/C++ toolchain prompt

**Issue Encountered**
VS Code's C/C++ extension showed a "select a compiler" walkthrough on first opening the Dev Container.

**Cause**
This is the extension's default onboarding flow for a fresh workspace; it expects a host-installed compiler in the usual case. It does not account for the fact that the actual toolchain lives inside the container image, not on the local machine.

**Resolution**
Dismissed the walkthrough ("Mark Done"). The Zephyr base image already contains the correct cross-compilers (Xtensa for ESP32-S3, ARM for the future Uno Q). Once `west build` runs once, it generates `compile_commands.json`, which the C/C++ extension reads automatically to resolve headers and enable IntelliSense — no manual compiler path configuration was needed.

---

### Issue 3 — usbipd not recognized on Windows host

**Issue Encountered**
```
usbipd : The term 'usbipd' is not recognized as the name of a cmdlet,
function, script file, or operable program. (in an Administrator PowerShell)
```

**Cause**
Windows 11 does not ship USB/IP support by default. The `usbipd-win` tool, which is required to share a physical USB device from Windows into WSL2, was not yet installed.

**Resolution**
Installed it via winget: `winget install dorssel.usbipd-win`. Closed and reopened a fresh Administrator PowerShell window afterward so the updated PATH was picked up, then confirmed with `usbipd list`, which correctly showed the XIAO board at BUSID `1-5` (VID:PID `303a:1001`, "USB JTAG/serial debug unit").

---

### Issue 4 — Container sees no serial device after USB attach

**Issue Encountered**
```
ls: cannot access '/dev/ttyACM*': No such file or directory
```
Run from inside the container terminal, even after `usbipd attach --wsl --busid 1-5` succeeded on the Windows side.

**Cause**
`usbipd` successfully attaches the USB device to the WSL2 kernel, but Docker Desktop runs containers in a separate utility VM that does not automatically inherit the `/dev` tree of the default WSL2 distribution. The device was visible to WSL2 itself but not yet passed into the container.

**Resolution**
Confirmed the device was visible at the WSL2 level first (`ls /dev/ttyACM*` outside the container returned `/dev/ttyACM0`). Stopped the container (`docker compose down`), added an explicit device mapping to `docker-compose.yml`:

```yaml
devices:
  - "/dev/ttyACM0:/dev/ttyACM0"
```

and brought it back up (`docker compose up -d`). The path then resolved correctly inside the container.

---

### Issue 5 — Flash runner rejected baud-rate flags (three attempts)

**Issue Encountered**
Three separate flag variants were rejected in sequence:

```
west flash --esp-flash-baud 921600
# -> FATAL ERROR: runner esp32 received unknown arguments

west flash --runner esp32 --baud-rate 921600
# -> FATAL ERROR: runner esp32 received unknown arguments

west flash --tool-opt="--baud 921600"
# -> FATAL ERROR: esp32 doesn't support --tool-opt option
```

**Cause**
None of these flags match the actual interface of Zephyr's `esp32` west runner. The runner has its own specific argument name (`--esp-baud-rate`, passed after a `--` separator) and does not support the generic `--tool-opt` passthrough that other runners use.

**Resolution**
Checked the runner's default behavior rather than fighting its flags: the `esp32` runner already defaults to 921600 baud internally. Ran plain `west flash` with no extra arguments at all, which completed successfully.

```bash
west flash
```

*(Noted for reference: an explicit override, if ever needed, is `west flash -- --esp-baud-rate <value>`.)*

---

## A.8 Result

`west flash` completed successfully against the XIAO ESP32-S3 over `/dev/ttyACM0`, and the on-board `USER_LED` (GPIO 21) began blinking at the configured 1-second interval, confirming the full pipeline end to end: **Windows 11 → WSL2 → Docker container → west build → USB passthrough → west flash → hardware**.

Validated chain:

1. VS Code (host) → Dev Containers extension → running Docker container
2. Zephyr SDK base image → `west init`/`update` → full RTOS source tree available in-container
3. Devicetree `led0` alias → resolved to GPIO 21 at compile time for `xiao_esp32s3/esp32s3/procpu`
4. `usbipd-win` (Windows) → WSL2 kernel → Docker device mapping → `/dev/ttyACM0` in-container
5. `west flash` → esptool handshake → firmware written → LED blinking

*Carrying this forward to the Uno Q: the Dockerfile, docker-compose.yml, and devcontainer.json require no changes. main.c is expected to remain unchanged as well, since it references the `led0` Devicetree alias rather than a raw pin. The only anticipated change is the board target passed to `west build` (`arduino_uno_q` in place of `xiao_esp32s3/esp32s3/procpu`), and re-confirming the serial device path for USB passthrough once the board is in hand.*
