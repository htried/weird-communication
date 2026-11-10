# Shopping list (laptop phases 3–4)

This list covers everything needed for the laptop environments (env1, env2 and env3). The
research server (boomhauer) and the daily-work Macs need nothing. Prices are rough as of
2026-11; check them before buying.

## Core (required)

| # | Item | Qty | ~Each | Notes |
|---|---|---|---|---|
| 1 | **x86 laptop** | 2 (identical) | $150–300 | Intel N100/N150/N200 or Ryzen, ≥8 GB RAM, ≥128 GB SSD. **Avoid** Snapdragon (ARM) and Core Ultra models with MIPI cameras. A used ThinkPad (T480/T490/X280) is a good alternative. Same arch as boomhauer, so container images build once. |
| 2 | **Powered USB 3 hub** (≥7 ports) | 2 | $25 | env2 puts 6+ USB devices on one laptop; cheap laptops have only 2–3 ports. |
| 3 | **USB webcam** (1080p, UVC) | 2 | $25–35 | One per laptop. Plain UVC works on Linux out of the box, and it can be aimed. Its built-in mic is not used. |
| 4 | **USB speaker** (a true USB audio device, not 3.5mm-only) | 2 | $15–25 | One per agent slot. Both go on one laptop in env2, one per laptop in env3. |
| 5 | **USB microphone** (plain desk/podcast mic, 48 kHz) | 2 | $20–30 | One per agent slot. **Avoid speakerphones and headsets with noise suppression or echo cancellation**: they filter out tones and data-carrying audio. |
| 6 | **USB Bluetooth 5 dongle** | 2 (identical) | $12–15 | One per agent slot. Check the chipset has Linux support (e.g. TP-Link UB500 / Realtek RTL8761B). The built-in Bluetooth gets disabled. |
| 7 | **USB flash drive** (≥16 GB) | 2 | $8 | Ubuntu installer. Before wiping Windows, boot a live USB and test audio, camera, Wi-Fi and Bluetooth while still inside the return window. |

## Observation

| # | Item | Qty | ~Each | Notes |
|---|---|---|---|---|
| 8 | **Room audio recorder**, independent of the laptops (Zoom H1n-class, or a phone) | 1 | $0–100 | 48 kHz is enough. The agents' audio hardware is 48 kHz, so nothing they emit is above ~24 kHz. No ultrasonic gear is needed. |
| 9 | **Phone tripod** | 1 | $20 | Room video backup. |

## Nice to have

| # | Item | Qty | ~Each | Notes |
|---|---|---|---|---|
| 10 | **USB Ethernet adapter + cable** | 2 | $15 | Keeps the host uplink off Wi-Fi during trials. Only useful if the room has wired ports. Optional, because agents can't control the host's Wi-Fi anyway. |
| 11 | **Laptop stands + floor tape** | 2 | $20 | Reproducible geometry across trials (distance, screen-to-camera angle). |
| 12 | **Power strip** | 1 | $15 | Laptops, hubs and speakers all need outlets. |

**Total:** about **$550–950** for the core and observation items (mostly the laptops), plus
about $100 for the nice-to-haves.

## How the devices map onto the environments

This follows the device rule in the design doc, §2.

| | env1 (one laptop) | env2 (one laptop) | env3 (two laptops) |
|---|---|---|---|
| webcam | 1, shared | 1, shared (contention) | 1 per laptop |
| speaker + mic | 1 pair, shared through the PipeWire mixer | 1 pair **per agent** | 1 pair per laptop |
| Bluetooth dongle | 1, shared | 1 **per agent** | 1 per laptop |

Two of each per-agent item covers every environment.
