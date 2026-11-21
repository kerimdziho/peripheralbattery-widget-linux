# Adding a wireless mouse / keyboard that Linux cannot read yet

Many 2.4 GHz gaming mice and keyboards (Compx, Scyrox, Attack Shark, VGN, Redragon, …) report their battery through a
**private message** on a vendor-defined HID channel. Linux does not know that message, so `upower` shows nothing.

Nobody can read "any brand" automatically - but once the message is known for **one** model, a tiny JSON *profile*
makes Peripheral Battery read it, for everyone with that model. This guide shows how to find it.

> **Only send bytes you captured from the vendor's own tool for this exact device.** Random data sent to a device can change
> its settings. Reading (listening) is always safe.

## 1. Find the right channel

```bash
peripheral-battery hid-probe
```

Lists every `/dev/hidrawN` with vendor:product (same ids as `lsusb`), its interface and usage pages. The dongle usually shows
several entries (mouse, keyboard, …). The battery/config channel is normally the one with a **vendor-defined usage page**
(`0xff00` and up) - the tool marks it.

## 2. Get access (once)

```bash
peripheral-battery hid-udev 3554:f5f7      # prints the exact commands for your ids
```

It prints a udev rule + `sudo` commands. Afterwards re-plug the dongle; no root is needed to read it.

## 3. Watch what the device says

```bash
peripheral-battery hid-probe --device /dev/hidraw3 --listen 30
```

Move the mouse, press buttons, plug / unplug the charging cable. Some devices announce their battery on their own; the tool
prints each report in hex and shows which bytes **changed** since the last one of the same type. A byte that tracks the
battery (e.g. `0x64` = 100 %) and jumps when you plug in the cable is your candidate.

If nothing battery-like appears, the device must be asked. Capture the question with the vendor's own tool:

* Install Wireshark, `sudo modprobe usbmon`, capture on the USB bus of the dongle, and use the vendor's driver/web app while it
  shows the battery. Look for an *output / SET_REPORT* going to the dongle and the *input / GET_REPORT* reply that contains the
  percentage.
* Try the question with `hid-probe --device /dev/hidrawN --send "08 04 00" --yes --listen 2` (it prints the answer).

## 4. Write the profile

Save as `~/.config/peripheral-battery/profiles/my-mouse.json` (or send it as a pull request to
`plasmoid/contents/code/profiles/`):

```json
{
  "name": "Scyrox 8K",
  "kind": "mouse",
  "match":    { "vendor": "3554", "product": "f5f7", "usage_page": "ff00" },
  "request":  { "type": "output", "hex": "08 04 00 00 00 00 00 00 00 00 00 00 00 00 00 00 49" },
  "response": { "type": "input", "prefix": "08 04", "timeout_ms": 300, "sum_equals": "55" },
  "percent":  { "byte": 6, "min": 0, "max": 100 },
  "charging": { "byte": 7 }
}
```

This is the layout of the **Compx** family (many Scyrox, Attack Shark, VGN, Dareu, Fantech … 2.4 GHz mice use it): a 17-byte
frame, report id `08`, command `04` = "get power", last byte = `0x55 - sum(first 16 bytes)`, reply has the percentage in
byte 6 and a charging flag in byte 7. **Whether it applies to your exact model is untested** - see `docs/candidates/` for two unverified
starting points for the Scyrox `3554:f5f7` dongle and test them with `hid-probe` first.

| Field | Meaning |
| --- | --- |
| `match.vendor` / `product` | hex ids from `lsusb`, as strings |
| `match.usage_page` | optional, hex; picks the right hidraw node of the dongle (recommended) |
| `match.interface`, `match.name_contains` | optional extra filters |
| `request` | optional message to send first: `type` `output` or `feature`, `hex` bytes (start with the report id, `00` if the device has none) |
| `response.type` | `input` (wait for a report) or `feature` (read a feature report: needs `report_id` and `length`) |
| `response.prefix` | optional hex the reply must start with - ignores unrelated mouse movement reports |
| `response.sum_equals` | optional hex; reply is accepted only if all its bytes add up to this value mod 256 (checksummed frames) |
| `percent.byte` | index of the byte (0 = first, report id included) |
| `percent.mask`, `shift` | bits to keep (hex string) and right-shift afterwards |
| `percent.min` / `max` | raw range mapped linearly to 0-100 % (e.g. `1`-`5` for a 5-step device) |
| `charging` | optional; charging if `(byte & mask) != 0`, or `== equals` when `equals` is given |
| `delay_ms` | optional pause between request and a feature read |

JSON **numbers are decimal, strings are hex** (`"mask": "7f"` and `"mask": 127` are the same).

## 5. Test it

```bash
peripheral-battery doctor      # shows loaded profiles and any error in them
peripheral-battery list
```

A sleeping mouse does not answer; it simply is not listed until it wakes (move it).

## Limits

* Devices that report a battery *voltage* instead of a percentage cannot be mapped with a simple linear range yet.
* One profile describes one device family; all matching profiles are tried in turn and the first that gets a valid answer wins.
* The widget polls every 60 s by default, so profiles should answer within `timeout_ms` (default 600 ms).
