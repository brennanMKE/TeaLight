# IR Tea Light: Design Spec

A rechargeable, IR-controlled flameless tea light built around an ATtiny85. Home Assistant owns the schedule and sends IR commands through an ESP32 blaster. Each light only has to receive commands, flicker, and manage its battery. Because the schedule lives in HA, the ATtiny's internal oscillator is accurate enough.

## Goals

- Fit a standard tea light footprint (about 38 mm diameter shell, PCB 32 mm or smaller)
- Rechargeable LiPo, charged over USB-C (optional pogo pads for a charging tray)
- 38 kHz IR receiver, NEC protocol
- Candle flicker on two warm LEDs
- Schedule controlled by Home Assistant; a local fallback timer when HA is not used
- Programmable after assembly through ISP pads

## System overview

```
Home Assistant (schedule: sunset trigger, off after 6 h)
    -> ESPHome on ESP32-C3 + IR LED (remote_transmitter, NEC)
        -> IR broadcast to the room
            -> each tea light: TSOP75238 -> ATtiny85 -> LEDs
```

## Schematic

### Power

| Ref | Part | Notes |
|---|---|---|
| J1 | USB-C receptacle, 6-pin power-only (e.g. GCT USB4125 or similar) | VBUS, GND, CC1, CC2 only |
| R1, R2 | 5.1 kΩ 0402 | CC1 and CC2 to GND, so chargers supply 5 V |
| U2 | MCP73831T-2ACI/OT, SOT-23-5 | 4.2 V single-cell LiPo charger |
| R3 | 10 kΩ 0402 | PROG resistor, sets about 100 mA charge current (charge current = 1000 V / R_PROG) |
| C1, C2 | 4.7 µF 0603 | MCP73831 VIN and VBAT |
| BT1 | LiPo cell **with built-in protection PCM**, 3.7 V, 100 to 200 mAh (e.g. 402030 or 302030 size) | JST-SH 1.0 mm pads, or solder pads |
| SW1 | Optional tiny slide switch in series with BT1 | Shipping and storage cutoff |

No regulator. The ATtiny85 runs directly from VBAT (3.0 to 4.2 V), which is within its 2.7 to 5.5 V range at 8 MHz.

### MCU

| Ref | Part | Notes |
|---|---|---|
| U1 | ATtiny85-20SU, SOIC-8 | Internal 8 MHz clock, BOD at 2.7 V |
| C3 | 100 nF 0402 | Decoupling at VCC |
| R4 | 10 kΩ 0402 | RESET pull-up |

### IR receiver

| Ref | Part | Notes |
|---|---|---|
| U3 | Vishay TSOP75238TT (SMD, **top view**, 38 kHz) | Pins: 1 and 4 = GND, 2 = VS, 3 = OUT. Confirm the TT (top view) suffix; TR is side view |
| R5 | 100 Ω 0402 | VS supply filter |
| C4 | 4.7 µF 0603 | VS supply filter, per Vishay's application circuit |

The RC filter matters here because the LEDs are PWM-switched on the same supply. OUT has an internal pull-up, so no external resistor is needed.

### LEDs

| Ref | Part | Notes |
|---|---|---|
| D1, D2 | Warm white (about 2200 K) or amber, 0603 or PLCC-2 3528 | Mounted at board center, under the flame diffuser |
| R6, R7 | 150 Ω 0402 (tune) | About 3 to 8 mA per LED from 3.3 to 4.2 V. Driven directly from GPIO, no MOSFET |

LED brightness falls as the battery discharges. Firmware compensates by measuring VCC (see firmware).

### Pin map

| ATtiny85 pin | Port | Function | ISP role |
|---|---|---|---|
| 1 | PB5 | RESET (10 kΩ pull-up) | RESET |
| 2 | PB3 | MCP73831 STAT (input, internal pull-up): low = charging | |
| 3 | PB4 | Spare: test pad (future button or light sensor) | |
| 4 | GND | GND | GND |
| 5 | PB0 | LED D1, PWM OC0A | MOSI |
| 6 | PB1 | LED D2, PWM OC0B | MISO |
| 7 | PB2 | IR OUT, INT0 / PCINT2 | SCK |
| 8 | VCC | VBAT | VCC |

ISP note: the LEDs on MOSI and MISO, and the TSOP on SCK, share the programming lines. This is normally fine with a USBasp because the loads are light (series resistors on the LEDs, weak internal pull-up on the TSOP). If programming is unreliable, add 1 kΩ series resistors between the ISP pads and PB0/PB1/PB2, or program before populating U3.

### ISP pads

Six 1 mm pads on the bottom side in a 2x3 grid at 2.54 mm pitch for a pogo-pin jig: VCC, GND, RESET, SCK, MISO, MOSI. Optionally match the Tag-Connect TC2030 footprint instead.

### Optional charging tray pads

Two large bottom pads (VBUS, GND) in parallel with J1, so a 12-slot tray with pogo pins can charge every unit without plugging in 12 cables. Add a Schottky diode (e.g. BAT54) from the pad VBUS to the charger input, so an exposed pad can't short the USB-C VBUS.

## PCB

- Round, 32 mm diameter (measure the MATULSA shell interior and adjust), 2-layer, 1.0 mm FR4 to save height
- **Top:** D1 and D2 at center, U3 near center with a clear view upward, U1, the passives
- **Bottom:** J1 at the edge, aligned with a slot in the shell; ISP pads; tray pads; battery pads
- Battery sits under the PCB, held by a 3D-printed carrier or double-sided foam tape
- Keep U3 at least 3 mm from the LEDs, and add a small printed light baffle if the LEDs cause false triggers
- Test that 940 nm IR passes through the diffuser shell. Most white or translucent plastics pass enough at room range. If yours doesn't, cut a small window or use clear plastic above U3.

## Power budget

| State | Current | Per day |
|---|---|---|
| On, flicker, 2 LEDs (about 6 mA average) + TSOP + MCU idle | about 7 mA | 6 h = 42 mAh |
| Off, ATtiny power-down + TSOP listening (about 0.35 to 0.5 mA, check the datasheet) | about 0.5 mA | 18 h = 9 mAh |
| **Total** | | **about 50 mAh/day** |

A 150 mAh cell gives about 3 nights. To stretch that:
- Lower LED current. Flicker reads as candle-like even when dim.
- Stretch goal: duty-cycle the TSOP's power from a GPIO (it would need PB4), and have HA repeat each command for 1 to 2 s so a sleeping receiver still catches it. This could cut standby current about 10x. Check the TSOP power-up settling time first.

## Firmware

Toolchain: ATTinyCore (Spence Konde), PlatformIO or Arduino IDE. Fuses: internal 8 MHz, BOD 2.7 V, EESAVE on.

### Libraries

- IRremote's `TinyIRReceiver.hpp` (NEC only, interrupt-driven, small). Configure `IR_RECEIVE_PIN` = PB2.

### State machine

```
OFF (power-down sleep, wake on IR pin change)
  -> ON command      -> ON
  -> TIMER command   -> ON with local 6 h countdown
ON (idle sleep between PWM updates, Timer0 PWM running)
  -> OFF command or countdown expires -> OFF
CHARGING (STAT low): LEDs off, or a slow breathing pulse as a charge indicator
LOW_BATTERY (VCC < 3.3 V): one dim blink every 10 s, then OFF; ignore ON until charged
```

### Flicker

- Two independent channels (OC0A, OC0B) so the "flame" moves
- 16-bit LFSR noise, low-pass filtered, with occasional dips to imitate a draft
- Update every 20 to 40 ms
- Scale brightness by measured VCC so it stays even across discharge

### Battery measurement (no pin needed)

Read the internal 1.1 V bandgap against VCC as the ADC reference: `VCC = 1.1 * 1024 / ADC`. Calibrate the bandgap per board if accuracy matters (it varies by about ±10%).

### Command set (NEC)

Use a custom NEC address so TV remotes don't trigger the lights. Proposed address `0x5A` (verify it doesn't conflict with devices in the room).

| Command | Action |
|---|---|
| 0x01 | On |
| 0x02 | Off |
| 0x03 | Brightness up |
| 0x04 | Brightness down |
| 0x05 | Mode: flicker / steady |
| 0x06 | On with local 6 h timer (fallback without HA) |
| 0x10 to 0x1F | Group on (group ID stored in EEPROM) |
| 0x20 to 0x2F | Group off |

### Learn mode (for generic remotes)

On USB unplug (STAT goes high), the light blinks twice and opens a 10 s window. The next two NEC codes received are stored in EEPROM as alternate On and Off. This lets any spare NEC remote, including the MATULSA remote, control the lights.

### Group assignment

While in learn mode, a third code from the custom address (`0x5A`, 0x30 to 0x3F) sets the group ID.

## Home Assistant side

### IR blaster (ESPHome)

- ESP32-C3 board + 1 or 2 940 nm IR LEDs (e.g. Vishay TSAL6200) driven by an NPN or logic-level N-MOSFET at about 100 mA pulsed
- Mount high in the room, aimed toward the lights; use two LEDs aimed differently for coverage

```yaml
remote_transmitter:
  pin: GPIO4
  carrier_duty_percent: 50%

button:
  - platform: template
    name: "Tea Lights On"
    on_press:
      - remote_transmitter.transmit_nec:
          address: 0x5A
          command: 0x01
          repeat:
            times: 3
            wait_time: 40ms
  - platform: template
    name: "Tea Lights Off"
    on_press:
      - remote_transmitter.transmit_nec:
          address: 0x5A
          command: 0x02
          repeat:
            times: 3
            wait_time: 40ms
```

Before building on this, check the current ESPHome docs for the exact form of `transmit_nec`: ESPHome changed how NEC address and command bytes are encoded in a past release, and the repeat syntax should be confirmed too. Verify the codes the firmware actually decodes with TinyIRReceiver's debug output.

### Automation

```yaml
automation:
  - alias: Tea lights at sunset
    trigger:
      - platform: sun
        event: sunset
    action:
      - service: button.press
        target: { entity_id: button.tea_lights_on }
      - delay: "06:00:00"
      - service: button.press
        target: { entity_id: button.tea_lights_off }
```

Better: use a separate automation with a time trigger for Off (sunset + 6 h computed by a template), so an HA restart during the 6 h delay doesn't leave the lights on. The lights can also get a 7 h local watchdog timer after each On as a safety net.

Alternative transmitter: a Broadlink RM4 mini, using HA's Broadlink integration, if you don't want to build the blaster.

## BOM (per light)

| Ref | Part | Package | Qty |
|---|---|---|---|
| U1 | ATtiny85-20SU | SOIC-8 | 1 |
| U2 | MCP73831T-2ACI/OT | SOT-23-5 | 1 |
| U3 | TSOP75238TT | SMD top view | 1 |
| J1 | USB-C 6-pin power-only receptacle | SMD | 1 |
| D1, D2 | Warm white or amber LED | 0603 / 3528 | 2 |
| BT1 | 3.7 V LiPo with protection, 100 to 200 mAh | pouch | 1 |
| R1, R2 | 5.1 kΩ | 0402 | 2 |
| R3, R4 | 10 kΩ | 0402 | 2 |
| R5 | 100 Ω | 0402 | 1 |
| R6, R7 | 150 Ω (tune) | 0402 | 2 |
| C1, C2, C4 | 4.7 µF | 0603 | 3 |
| C3 | 100 nF | 0402 | 1 |
| SW1 | Slide switch (optional) | SMD | 0 to 1 |
| D3 | BAT54 (optional, tray pads) | SOT-23 | 0 to 1 |

## Tasks for Claude Code

1. Create a KiCad 8 or 9 project `tea-light/` with the schematic above. Use stock KiCad symbols and footprints where available; create a footprint for TSOP75238TT from the Vishay datasheet drawing.
2. Run ERC and fix all errors.
3. Lay out a 32 mm round 2-layer board with the placement rules above. Run DRC against the JLCPCB 2-layer rules.
4. Generate Gerbers, drill files, BOM, and CPL files for JLCPCB assembly; map parts to LCSC part numbers and flag any that aren't in stock.
5. Create a PlatformIO project `firmware/` for the ATtiny85 (ATTinyCore) implementing the state machine, flicker, VCC measurement, command set, and learn mode.
6. Create an ESPHome config `ir-blaster.yaml` and the HA automations.
7. Write a bring-up checklist: program fuses, verify IR decode with serial debug on a dev board first, measure standby and on current with a meter, and test range through the shell.

## Open questions

- Interior dimensions of the MATULSA shell, and whether its diffuser passes IR well
- Single LED or two (two give better flicker; one saves power)
- USB-C per unit, or a charging tray with pogo pads, or both
- Whether the TSOP standby current justifies the duty-cycling stretch goal
