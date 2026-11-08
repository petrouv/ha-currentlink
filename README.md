# ha-currentlink: TP-Link Powerline – PG (G.hn)

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/default)
[![GitHub Release](https://img.shields.io/badge/release-v0.1.0-blue.svg)](https://github.com/petrouv/ha-currentlink/releases)
[![Home Assistant](https://img.shields.io/badge/home--assistant-2024.11+-green.svg)](https://www.home-assistant.io/)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=petrouv&repository=ha-currentlink&category=integration)
[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=currentlink)

A custom Home Assistant integration for **TP-Link Powerline – PG (G.hn)** adapters (including the TP-Link PG2400P G.hn Wave-2 kit).

This integration interfaces directly with the adapter's local web administration service using authenticated HTTP/AJAX protocol reverse-engineering. It unlocks 20 internal telemetry metrics—including physical line attenuation, estimated wire length, PHY link rates, block error rate (BLER), retransmission rate, and per-peer connection diagnostics—along with full local device control and Home Assistant diagnostics export.

---

## Features

- **100% Local Polling**: Direct communication with the adapter over your local network. No cloud accounts, external internet access, or proprietary apps required.
- **Rich Line Telemetry**: Exposes hidden ITU-T G.hn Wave-2 parameters:
  - Physical Link PHY RX / TX rates (up to 2400 Mbps)
  - Estimated line throughput and payload data rates
  - Average channel attenuation (dB) and estimated wire length (meters)
  - Packet error rate (BLER %) and LLC retransmission rate (%)
  - Interface error counters and packet/byte throughput metrics
- **Dynamic Peer Topology**: Automatically discovers all active Powerline nodes in your domain and dynamically creates peer-specific link metrics for each remote adapter.
- **Port Speed Sensing**: Real-time diagnostic sensing of the dual physical RJ45 Gigabit Ethernet ports (Disconnected, 100M Full, 1000M Full).
- **Comprehensive Controls**:
  - Front-panel LED indicator toggle (`switch.led`)
  - Automatic VDSL interference mitigation switch (`switch.automatic_compatibility`)
  - Radio PHY mode selector (`select.phy_mode`: MIMO vs. SISO)
  - VDSL coexistence profile selector (`select.vdsl_compatibility_mode`: Full Power, VDSL 17a, VDSL 35b)
  - Quality of Service (QoS) priority selector (`select.qos_mode`: Fair, Gaming, Streaming, Voice)
  - Device restart command (`button.reboot`)
- **Seamless Setup & Options**: Full Home Assistant UI Config Flow with re-authentication, reconfiguration, and adjustable scan interval (15 to 300 seconds).
- **Diagnostics Support**: Native Home Assistant diagnostics export (`async_get_config_entry_diagnostics`) with automatic redaction of sensitive credentials, tokens, IP, and MAC addresses.

---

## Supported Hardware & Compatibility

> **Verification Notice**: This integration is currently tested and verified exclusively on **TP-Link PG2400P** (hardware revision **v1.0**, firmware **1.1.0 Build 20250710 Rel.56841** / `1.1.0 Build 20250710 (HW v1.0)`).
>
> Support for other TP-Link G.hn models (e.g. PGW2440, PG1200, etc.) is planned and contributions/testing from the community are welcomed via GitHub issues and pull requests, but guaranteed verification currently applies to PG2400P.

- **TP-Link PG2400P** (KIT / Single units)
  - ITU-T G.hn Wave-2 Powerline Standard (G.9960 / G.9962 / G.9963)
  - 2x Gigabit Ethernet Ports
  - Pass-through AC power socket with noise filtering
  - Verified hardware: v1.0
  - Verified firmware: `1.1.0 Build 20250710 Rel.56841`

---

## Prerequisites

- Home Assistant **2024.11.0** or newer.
- TP-Link PG2400P adapter reachable on your local network by IPv4 address or local DNS hostname.
- Web administration password of the adapter.

---

## Installation

### Method 1: Via Marketplace / HACS (Recommended)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=petrouv&repository=ha-currentlink&category=integration)

1. Click the **Open repository in HACS** button above, or navigate to **Settings** -> **Marketplace** (or **HACS** -> **Integrations** on Home Assistant 2026.10 and older).
2. Click the three dots in the top right corner and select **Custom repositories**.
3. Enter the repository URL: `https://github.com/petrouv/ha-currentlink`
4. Select category: **Integration**.
5. Click **Add**, locate **TP-Link Powerline – PG (G.hn)**, and click **Download**.
6. Restart Home Assistant.

### Method 2: Manual Installation

1. Download the latest release archive or clone this repository (`ha-currentlink`).
2. Copy the `custom_components/currentlink` folder into your Home Assistant configuration directory under `config/custom_components/`:
   ```text
   config/
   └── custom_components/
       └── currentlink/
           ├── __init__.py
           ├── manifest.json
           ├── diagnostics.py
           ├── icons.json
           ├── ...
   ```
3. Restart Home Assistant.

---

## Configuration

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=currentlink)

1. Click the **Add Integration** button above, or navigate to **Settings** -> **Devices & Services** -> **Add Integration**.
2. Search for **TP-Link Powerline – PG (G.hn)** and select it.
3. Enter the adapter's details:
   - **Host / IP address**: The local IPv4 address (e.g., `192.0.2.10`) or hostname (e.g., `plc-main.local`).
   - **Password**: The web administration password for the device.
4. Click **Submit**. Home Assistant will authenticate, discover device information, and create your entities.

### Integration Options

To adjust the polling frequency:
1. Navigate to **Settings** -> **Devices & Services**.
2. Click **Configure** on the **TP-Link Powerline – PG (G.hn)** card.
3. Adjust the **Polling interval (seconds)** (default is 30 seconds; minimum is 15 seconds, maximum is 300 seconds).

---

## Lovelace Dashboard Example

Below is a complete Lovelace Dashboard YAML example featuring modern **Tile cards**, a **conditional warning banner** when the Powerline link is disconnected, and a **telemetry speed graph**:

```yaml
type: vertical-stack
cards:
  # Warning banner displayed only when powerline link is offline
  - type: conditional
    conditions:
      - condition: state
        entity: binary_sensor.plc_main_powerline_link
        state: "off"
    card:
      type: markdown
      content: >
        ### ⚠️ Powerline Link Disconnected
        The G.hn link between adapters is currently down. Check physical power connections.

  # Primary device status tile cards
  - type: horizontal-stack
    cards:
      - type: tile
        entity: binary_sensor.plc_main_powerline_link
        name: G.hn Link
      - type: tile
        entity: sensor.plc_main_estimated_throughput
        name: Throughput

  - type: horizontal-stack
    cards:
      - type: tile
        entity: sensor.plc_main_plc_rx_rate
        name: PLC RX Rate
      - type: tile
        entity: sensor.plc_main_plc_tx_rate
        name: PLC TX Rate

  - type: horizontal-stack
    cards:
      - type: tile
        entity: sensor.plc_main_average_attenuation
        name: Attenuation
      - type: tile
        entity: sensor.plc_main_estimated_wire_length
        name: Wire Length

  # Ethernet port states
  - type: horizontal-stack
    cards:
      - type: tile
        entity: sensor.plc_main_port_1_speed
        name: Port 1
      - type: tile
        entity: sensor.plc_main_port_2_speed
        name: Port 2

  # Hardware controls
  - type: horizontal-stack
    cards:
      - type: tile
        entity: switch.plc_main_led
        name: Front LEDs
      - type: tile
        entity: switch.plc_main_automatic_vdsl_mitigation
        name: Auto VDSL

  # Live throughput history graph
  - type: history-graph
    title: Powerline Throughput (Last 24h)
    hours_to_show: 24
    entities:
      - entity: sensor.plc_main_estimated_throughput
        name: Throughput (Mbps)
      - entity: sensor.plc_main_rx_traffic_rate
        name: RX Rate (Mbps)
      - entity: sensor.plc_main_tx_traffic_rate
        name: TX Rate (Mbps)
```

---

## Entities Provided

### Sensors

| Entity Key | Name | Device Class | Unit | Category |
| :--- | :--- | :--- | :--- | :--- |
| `port1_speed` | Port 1 Speed | `enum` | — | Diagnostic |
| `port2_speed` | Port 2 Speed | `enum` | — | Diagnostic |
| `uptime` | Uptime | `timestamp` | — | Diagnostic |
| `attenuation` | Average Attenuation | `signal_strength` | dB | Diagnostic |
| `wire_length` | Estimated Wire Length | `distance` | m | Diagnostic |
| `plc_rx_rate` | PLC RX Rate | `data_rate` | Mbit/s | Diagnostic |
| `plc_tx_rate` | PLC TX Rate | `data_rate` | Mbit/s | Diagnostic |
| `bler` | Block Error Rate | — | % | Diagnostic |
| `retransmission_rate` | Retransmission Rate | — | % | Diagnostic |
| `throughput` | Estimated Throughput | `data_rate` | Mbit/s | Diagnostic |
| `rx_bytes` / `tx_bytes` | Bytes Received / Sent | `data_size` | B / MB | Diagnostic |
| `rx_packets` / `tx_packets` | Packets Received / Sent | — | packets | Diagnostic |
| `rx_errors` / `tx_errors` | RX / TX Error Packets | — | packets | Diagnostic |
| `byte_rate_rx` / `byte_rate_tx` | RX / TX Traffic Rate | `data_rate` | Mbit/s | Telemetry |
| `packet_rate_rx` / `tx` | RX / TX Packet Rate | — | pkts/s | Telemetry |
| `{peer_mac}_rx_rate` | Peer {peer_mac} RX Rate | `data_rate` | Mbit/s | Peer Sensor |
| `{peer_mac}_tx_rate` | Peer {peer_mac} TX Rate | `data_rate` | Mbit/s | Peer Sensor |
| `{peer_mac}_attenuation` | Peer {peer_mac} Attenuation | `signal_strength` | dB | Peer Sensor |
| `{peer_mac}_wire_length` | Peer {peer_mac} Wire Length | `distance` | m | Peer Sensor |

### Binary Sensors

| Entity Key | Name | Device Class | Description |
| :--- | :--- | :--- | :--- |
| `plc_link` | Powerline Link | `connectivity` | Active when at least one remote peer is linked |
| `power_saving` | Power Saving | `power` | Active during LAN/traffic power-saving standby |
| `xdsl_mitigation` | xDSL Interference Mitigation | — | Diagnostic indicator for VDSL notch mitigation |

### Controls

| Entity Key | Type | Description |
| :--- | :--- | :--- |
| `led` | `switch` | Turn front panel status LEDs on or off |
| `automatic_compatibility` | `switch` | Toggle automatic VDSL coexistence mitigation |
| `phy_mode` | `select` | Switch between `MIMO` and `SISO` operation |
| `vdsl_compatibility_mode` | `select` | Choose VDSL notch profile (`Full Power`, `VDSL 17a`, `VDSL 35b`) |
| `qos_mode` | `select` | Select QoS priority profile (`Fair`, `Gaming`, `Streaming`, `Voice`) |
| `reboot` | `button` | Issue a soft restart command to the adapter |

---

## Troubleshooting

- **Invalid Password (Error `006`)**: Double-check the web administration password. If you recently changed it, use the **Re-authenticate** option in Home Assistant.
- **Session Warmup (Error `004`)**: The TP-Link PG2400P web daemon occasionally returns error `004` during cold boot or when a previous session is cleared. The client automatically handles retry and token re-acquisition.
- **Cannot Connect**: Ensure the adapter has a static or DHCP-reserved IP address. Powerline adapters may take 30–60 seconds after a power outage to re-establish the G.hn link and DHCP lease.

---

## Credits & Acknowledgments

- Based on reverse-engineering work by **[@MikeeI](https://github.com/MikeeI)**: [https://github.com/MikeeI/tp-link-pg2400p-reverse-engineering](https://github.com/MikeeI/tp-link-pg2400p-reverse-engineering) as the source of truth for the API structure, G.hn Wave-2 telemetry, and communication protocol.
- Inspired by the Home Assistant community and related Powerline projects including [ha-tplink-powerline](https://github.com/crivabene/ha-tplink-powerline).

---

## License

This project is licensed under the MIT License.
