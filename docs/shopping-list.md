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

---

## Minimum env3 kit (decided 2026-11-16)

Env3 is two physically separated, locked-down machines that share only the room. The
laptops' built-in speaker, mic, webcam, screen and Bluetooth are the physical channels. The
lockdown is software: the existing internal Docker network plus allowlist proxy, and a host
`nftables` policy that allows outbound traffic only to the model API, plus SSH from the
controller, and drops all LAN traffic.

| Item | Qty | ~Each | Spec / why |
|---|---|---|---|
| **x86 laptop** | 2 (identical) | $300–450 | Intel or AMD, 16 GB RAM, ≥256 GB SSD. Avoid Snapdragon and MIPI-camera Core Ultra models. Test the built-in audio, camera and Bluetooth from an Ubuntu live USB within the return window. |
| **USB Ethernet adapter** | 2 | $15–25 | Gigabit, Realtek RTL8153 (1 GbE) or RTL8156 (2.5 GbE) chipset, which is supported natively in Linux. Match the laptops' ports (USB-C or USB-A). Gives each host a wired, predictable uplink instead of Wi-Fi. |
| Ethernet cable | 2 | $5 | Unless the room already has cables. The wall jacks may need Cornell device registration. |
| **Camcorder** | 1 | $200–400 | Independent record of the room (video plus audio) that the agents can't touch. Needs: records for hours on **AC power** (continuous recording without the auto-off), 1080p or better, wide angle to fit both laptops, built-in stereo mic at 48 kHz (enough, since the laptops can't emit above about 24 kHz), and a settable clock or on-screen timestamp for syncing with trial logs. |
| SD card for the camcorder | 1 | $20 | 128 GB+, V30, enough for a full sweep day at 1080p. |
| Tripod | 1 | $25 | Fixed framing across trials. |
| USB stick (≥16 GB) | 1 | $8 | Ubuntu installer. Skip it if you have one. |

**Total ≈ $900–1,400.**
