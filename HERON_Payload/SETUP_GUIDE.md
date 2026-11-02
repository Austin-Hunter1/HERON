# HERON NUC: complete setup guide, from a Windows laptop

Wipe the NUC, install Ubuntu Server (no desktop), run it headless over one Ethernet cable from your
Windows laptop, and record from the planner. Works with any mix of **B210** and **B200** radios.

Every command says **where** to type it:

* **[Windows]** = **PowerShell** on your laptop (Start → type `PowerShell` → open *Windows PowerShell*)
* **[NUC console]** = monitor + keyboard plugged into the NUC (only Part 3)
* **[NUC]** = after `ssh heron@192.168.137.10` in PowerShell; the prompt looks like `heron@heron-nuc:~$`

Copy a whole grey box at a time. Lines starting with `#` are notes.

**What you need:** NUC, USB stick ≥ 8 GB (gets erased), monitor + USB keyboard (once), an Ethernet cable,
a USB‑to‑Ethernet adapter if your laptop has no Ethernet port, the radios with USB 3 cables, a 3.3 V
USB‑UART adapter, the Cube. Time: about 2 hours.

---

## How the networking works (read once)

* **Your laptop keeps using school Wi‑Fi for internet. Nothing in this guide changes your Wi‑Fi.**
  Installing Ubuntu happens only on the NUC; your laptop's operating system isn't touched.
* The NUC has no Wi‑Fi. It talks to your laptop over **one Ethernet cable**, plugged straight from the
  NUC into your laptop.
* The NUC always has the address **192.168.137.10**. Your laptop's Ethernet port is **192.168.137.1**.
* When the NUC needs internet (installing, updates), Windows **shares** your Wi‑Fi with it over the cable
  ("Internet Connection Sharing"). At the field you just turn sharing off; the addresses stay the same.

---

## Part 0 — Get the files onto Windows

1. Download **HERON_Payload.zip** (sent in the chat) to `Downloads`.
2. Right‑click it → **Extract All…** → Extract. You now have `Downloads\HERON_Payload\HERON_Payload\…`.
   Move the inner `HERON_Payload` folder so it is `C:\Users\<you>\HERON_Payload`
   (open File Explorer, type `%USERPROFILE%` in the address bar to get there).
3. [Windows] Check SSH is built in:
   ```powershell
   ssh -V
   ```
   Prints `OpenSSH_for_Windows_…`. If it says *not recognized*: Settings → System → Optional features →
   View features → add **OpenSSH Client**, then reopen PowerShell.

### 0b. See the Payload Setup page right now, on your laptop (no NUC, simulated radios)
Install Python once from https://www.python.org/downloads/ (tick **Add python.exe to PATH** in the
installer). Then in PowerShell:
```powershell
cd $HOME\HERON_Payload
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m heron_payload.agent --connect udpout:127.0.0.1:14599 --backend fake --state-dir $env:TEMP\heron --http 127.0.0.1:8080
```
Open **http://127.0.0.1:8080** in your browser. Everything works against pretend radios: Find radios,
Load onto payload, Record test (shows the signal histograms). Ctrl+C in PowerShell stops it.
Next time you only need the last line (after `cd $HOME\HERON_Payload`).
On the real NUC the same page is at **http://192.168.137.10:8080** (Part 7d onwards).

---

## Part 1 — Back up the old NUC  [Windows]

The old NUC must be on and on the lab network, like before (172.27.16.140). Your laptop must be on a
network that can reach it (the school network you used before).

```powershell
mkdir $HOME\HERON_old_nuc_backup
ssh gpsadmin@172.27.16.140 "tar czf /tmp/nuc_home.tgz --exclude=.cache --exclude=.mozilla -C /home gpsadmin -C / etc/rc.local etc/fstab; ls -lh /tmp/nuc_home.tgz"
scp gpsadmin@172.27.16.140:/tmp/nuc_home.tgz $HOME\HERON_old_nuc_backup\
dir $HOME\HERON_old_nuc_backup
```
Password both times: the old gpsadmin password. The file should be roughly 1–2 GB. Check it:
```powershell
tar -tzf $HOME\HERON_old_nuc_backup\nuc_home.tgz | Select-String rx_multi_to_file.cpp
```
Prints a path → backup is good.

If the NUC still has the old 2 TB data SSD with flights you want, tell me before Part 5 (it gets erased).
If the NUC belongs to the Sense Lab, check with them before wiping.

---

## Part 2 — Make the Ubuntu USB stick  [Windows]

1. Download **Ubuntu Server 24.04 LTS**: https://ubuntu.com/download/server → *Option 2: Manual
   installation* → `ubuntu-24.04.x-live-server-amd64.iso` (~3 GB).
2. Download **Rufus**: https://rufus.ie (the portable version is fine). Run it.
3. Rufus: **Device** = your USB stick · **Boot selection** = SELECT → the ISO ·
   **Partition scheme** = GPT · **Target system** = UEFI. Click **START**.
   If asked, choose **Write in ISO Image mode (Recommended)** → OK → OK (erases the stick).
4. When it says READY, close Rufus and eject the stick.

---

## Part 3 — Set the laptop's Ethernet and share internet  [Windows]

### 3a. Give your Ethernet port a fixed address (once)
Plug the USB‑Ethernet adapter in (if you use one). Then:
Settings → **Network & internet** → **Ethernet** (if several, the adapter you'll use for the NUC) →
**IP assignment: Edit** → *Manual* → **IPv4: On** →

* IP address: `192.168.137.1`
* Subnet mask: `255.255.255.0` (if it asks for *Subnet prefix length*, type `24`)
* Gateway: empty · Preferred DNS: empty → **Save**

This only changes the Ethernet port. Wi‑Fi is untouched.

### 3b. Share Wi‑Fi to the NUC (whenever the NUC needs internet)
Press Win+R → type `ncpa.cpl` → Enter. The *Network Connections* window opens.
Right‑click **Wi‑Fi** → Properties → **Sharing** tab → tick **Allow other network users to connect
through this computer's Internet connection** → *Home networking connection*: pick your **Ethernet**
adapter → OK. (Windows may say the LAN will be set to 192.168.137.1 — that's what we want → Yes.)

**Leave sharing switched on permanently.** At the field there's no Wi‑Fi to share, but the laptop's
Ethernet stays at 192.168.137.1, which is what the NUC expects. (If you ever untick it, Windows may reset
the Ethernet port to automatic; then redo 3a.)

If the school Wi‑Fi blocks sharing (the NUC gets no internet in Part 4c), use a wall Ethernet jack in the
lab for the NUC during Parts 3–7 instead, and find its address on the NUC console with `ip -4 addr`.

---

## Part 4 — Install Ubuntu on the NUC  [NUC console]

### 4a. Installer

1. Shut the NUC down. **Unplug the data SSD** (or its cable) if it has one.
2. Plug in: monitor, keyboard, the USB stick, and the **Ethernet cable from the NUC to your laptop**
   (with sharing on from 3b).
3. Power on and tap **F10** until a boot menu appears; choose the USB stick (the *UEFI* entry).
   (If the NUC boots the old system instead, power off and try again tapping F10 sooner. F2 = BIOS.)
4. Choose **Try or Install Ubuntu Server**, then on each screen:

| Screen | What to do |
|---|---|
| Language | English |
| Installer update available | **Continue without updating** |
| Keyboard | English (US) → Done |
| Choose type of installation | **Ubuntu Server** (not minimized) → Done |
| Network configuration | The Ethernet port (`eno1` or `enp…`) shows `DHCPv4 192.168.137.xx`. → Done. If it says *disabled* or nothing after a minute, check sharing (3b) and the cable. |
| Proxy | empty → Done |
| Ubuntu archive mirror | wait for "passed" → Done |
| Guided storage | **Use an entire disk** → the internal NVMe (~477 GB). Keep "Set up this disk as an LVM group" ticked → Done |
| Storage configuration | Done → **Continue** (erases the old system) |
| Profile | Your name `HERON` · Server name **`heron-nuc`** · Username **`heron`** · a password you'll remember |
| Upgrade to Ubuntu Pro | **Skip for now** → Continue |
| SSH configuration | **Tick "Install OpenSSH server"** · Import SSH key: none → Done |
| Featured server snaps | tick nothing → Done |

5. Wait for **Installation complete!** → **Reboot Now** → remove the USB stick when told → Enter.
### 4b. Fixed address for the NUC  [NUC console]
At `heron-nuc login:` log in as `heron`. Find the Ethernet port name:
```sh
IF=$(ip -o -4 route show default | awk '{print $5}'); echo $IF
```
It prints e.g. `eno1`. Then copy this whole box:
```sh
sudo tee /etc/netplan/60-heron.yaml >/dev/null <<EOF
network:
  version: 2
  ethernets:
    $IF:
      dhcp4: true
      optional: true
      addresses: [192.168.137.10/24]
EOF
sudo chmod 600 /etc/netplan/60-heron.yaml
sudo netplan apply
ip -4 addr show $IF | grep inet
```
You should see `192.168.137.10/24` (plus a DHCP address while sharing is on).
`optional: true` means the NUC doesn't wait at boot when no cable is plugged in.

From now on unplug the monitor and keyboard. Everything else is from the laptop.

---

## Part 5 — Headless login from Windows

### 5a. First login  [Windows]
```powershell
ssh heron@192.168.137.10
```
Type `yes` to the fingerprint question, then the heron password. Type `exit` to come back.

### 5b. Login without a password  [Windows]
```powershell
ssh-keygen -t ed25519
```
Press Enter at every question (skip it if it says the file already exists — answer `n`). Then:
```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh heron@192.168.137.10 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
ssh heron@192.168.137.10
```
The second command should log in with no password.

### 5c. Update  [NUC]
```sh
ping -c 2 ubuntu.com                         # must get replies; if not, see Part 3b
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y avahi-daemon htop tmux
```

### 5d. Settings for steady recording  [NUC]
```sh
# Bigger USB buffers for the radios (UHD recommends this)
sudo sed -i 's/GRUB_CMDLINE_LINUX_DEFAULT="/&usbcore.usbfs_memory_mb=1000 /' /etc/default/grub
sudo update-grub
# Never sleep
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
# No automatic updates/reboots in the field (update by hand on the bench)
sudo apt remove -y unattended-upgrades
# CPU always at full speed
sudo apt install -y cpufrequtils
echo 'GOVERNOR="performance"' | sudo tee /etc/default/cpufrequtils
sudo reboot
```
Wait a minute, `ssh heron@192.168.137.10` again, then check:
```sh
cat /proc/cmdline        # contains usbfs_memory_mb=1000
```

---

## Part 6 — Data SSD  [NUC]

Skip this part if there's no SSD yet: in Part 9 set *Save recordings in* to `/home/heron/recordings`.

```sh
sudo poweroff
```
Plug the SSD back in, power on, `ssh` back in.
```sh
lsblk -o NAME,SIZE,MODEL,MOUNTPOINT
```
The SSD is the ~1.8T disk, usually `sda`. The system disk is `nvme0n1` — **never** use that one.
**This erases the SSD:**
```sh
DISK=/dev/sda               # change if lsblk showed a different name
sudo parted -s $DISK mklabel gpt mkpart data ext4 0% 100%
sudo mkfs.ext4 -F -L DataStore ${DISK}1
sudo mkdir -p /media/DataStore
echo 'LABEL=DataStore /media/DataStore ext4 defaults,noatime,nofail 0 2' | sudo tee -a /etc/fstab
sudo systemctl daemon-reload && sudo mount -a
sudo mkdir -p /media/DataStore/heron && sudo chown -R heron:heron /media/DataStore
df -h /media/DataStore
```
Speed check (writes 5 GB, then deletes it):
```sh
dd if=/dev/zero of=/media/DataStore/speedtest bs=1M count=5000 oflag=direct status=progress; rm /media/DataStore/speedtest
```
Need comfortably more than your setup's write rate (88 MB/s for one B210 with two channels; Payload Setup
shows the number). A SATA SSD usually gives 300+ MB/s.

---

## Part 7 — Install the HERON payload service

### 7a. Copy the code  [Windows]
```powershell
scp -r $HOME\HERON_Payload heron@192.168.137.10:~/
```

### 7b. Install  [Windows]
Internet sharing must be on (Part 3b).
```powershell
ssh -t heron@192.168.137.10 "cd HERON_Payload && sudo bash deploy/install.sh"
```
Type the heron password when sudo asks. 5–15 minutes. It:
1. installs UHD 4.6 (`uhd-host`, `libuhd-dev`), compilers, Python; downloads the radio FPGA images;
   installs the USB rules so B200/B210 work without sudo;
2. puts the code in `/opt/heron`, builds the recorder (`/opt/heron/bin/heron-recorder`);
3. installs the `heron-payload` service, which starts at every boot.

It ends with `Installed.` If it stops with an error, send me the last 20 lines.

### 7c. Check  [NUC]
```sh
exit
```
then `ssh heron@192.168.137.10` again (so your login picks up the new groups), and:
```sh
systemctl status heron-payload --no-pager     # "active (running)"
journalctl -u heron-payload -n 20 --no-pager
```
*Cannot open Cube link … Retrying every 5 s* is expected until Part 10.

### 7d. Open Payload Setup  [Windows]
Browser: **http://192.168.137.10:8080** — top bar: *Payload: No setup loaded*, *Flight controller: not linked*.

---

## Part 8 — Radios (B210 and/or B200)  [NUC]

1. Plug each radio into a **blue USB 3 port** with a USB 3 cable. Several radios: spread them over
   different ports.
   Power: a B200 runs from USB. A B210 recording two channels can draw more than some ports give; if it
   disconnects or `uhd_usrp_probe` fails, use its external 6 V supply.
2. Check them:
   ```sh
   uhd_find_devices
   ```
   One block per radio, with `product: B210` or `B200` and its `serial`. Write the serials down.
   *No UHD Devices Found* → other port/cable; `lsusb | grep -i -e ettus -e 2500` should list it.
3. Load and test one:
   ```sh
   uhd_usrp_probe --args serial=<SERIAL> | head -30
   ```
   (Do this before loading a setup in Part 9; once loaded, the service is using the radios.)
4. Optional live spectrum in the terminal (Ctrl+C quits):
   ```sh
   /usr/libexec/uhd/examples/rx_ascii_art_dft --args serial=<SERIAL> --freq 1176.45e6 --rate 22e6 --gain 45 --ant RX2 --ref-lvl -50
   ```

Antenna ports: **B210** has RF A and RF B (record either or both). **B200** has only RF A. Use the RX2
connector unless you choose TX/RX in Payload Setup.

---

## Part 9 — Payload Setup  [Windows browser, http://192.168.137.10:8080]

1. **Setup** drop‑down, start from:
   * *L5 direct + reflected* — one B210, both channels;
   * *SURGE single antenna* — one B210, RF B only (like 2023);
   * *One B200 (single antenna)* — one B200, its only channel (RF A, RX2 port);
   * *Full payload (4 radios)* — two B200 + two B210, L1 and L5 (needs the external clock board).
2. **Find radios on USB.** It fills in serials by model (B210s into B210 rows, B200/B200mini into B200
   rows). Radios found but not in the setup → **Add them to this setup**. Rows with no radio: switch them
   off or remove them (✕). **Add B210 / Add B200** add rows by hand.
3. Per radio: band **L1 / L2 / L5** (or Other + centre MHz), or a collect-wizard **Profile**; sample rate, bandwidth. Both channels of a B210
   share its frequency and rate.
4. Per channel: on/off, antenna name (what's written on the cable), RX2/TX‑RX, gain.
5. **Reference clock:** *Internal* for radios recording independently; *External 10 MHz + PPS* only
   with a clock board wired to every radio.
6. **Save recordings in:** `/media/DataStore/heron` (button **Use data drive**) or the NUC's own SSD
   (button **Use NUC SSD**, `~/heron_recordings`). If the setup points at the data drive but nothing is
   mounted at `/media/DataStore`, the payload records to the NUC SSD instead of refusing: the top bar says
   *Recording to: NUC SSD*, the planner strip shows *(NUC SSD)* after the setup name, and *Keep free on disk*
   still protects the system drive.
7. Right panel: MB/s to disk, GB per minute, minutes that fit, USB load. Yellow note = check with a soak test.
8. **Load onto payload** → *Loaded on payload*, top bar *Payload: Ready*.
9. **Signal check → Record test** (antennas connected, outside or near a window for GPS). Each channel
   should read *Good level* or *Usable*. If it suggests a gain, click **Use … dB** → **Load onto
   payload** → test again.
   The length menu goes from 5 s to 30 min, or **Until I stop** (a **Stop** button appears while it runs).
   While it records it shows elapsed time, MB/s and the dropped-sample count.
10. **Save** under your own name. After any reboot the NUC loads the last loaded setup by itself.

---

## Part 10 — Cube on TELEM2

### 10a. Wiring (everything powered off)
**3.3 V** USB‑UART adapter ↔ Cube TELEM2 (pin 1 is at the red‑wire end):

| TELEM2 pin | Signal | Adapter pin |
|---|---|---|
| 1 | +5 V | **leave unconnected** |
| 2 | TX (from Cube) | **RX** |
| 3 | RX (to Cube) | **TX** |
| 4, 5 | CTS, RTS | unconnected |
| 6 | GND | **GND** |

Adapter USB → any NUC USB port.

### 10b. Cube parameters (Mission Planner → Config → Full Parameter List)
`SERIAL2_PROTOCOL = 2` · `SERIAL2_BAUD = 921` · `BRD_SER2_RTSCTS = 0` → Write Params → reboot the Cube.
Note `SYSID_THISMAV` (usually 1).

### 10c. Tell the service which port  [NUC]
```sh
ls -l /dev/serial/by-id/
sudo nano /etc/heron/payload.env
```
Set `HERON_CUBE_PORT=/dev/serial/by-id/<the name from ls>` (the installer may have filled it already) and
`HERON_SYSTEM_ID=<SYSID_THISMAV>`. Save: Ctrl+O, Enter, Ctrl+X.
```sh
sudo systemctl restart heron-payload
journalctl -u heron-payload -f           # Ctrl+C to stop watching
```
Payload Setup top bar → *Flight controller: STABILIZE* (or the current mode).

---

## Part 11 — Planner (on whichever computer runs HERON Ground Control)

1. If the planner computer still has the old payload files, delete:
   `HERON_Payload\heron_payload\gateway.py`, `api.py`, `wire.py` and
   `Specular_Planner\static\payload.js`, `payload.css`.
2. Start the planner, **Fly** → connect to the drone over the telemetry radio.
3. Within a few seconds the strip at the top says **Payload ready** + your setup name.
   *No signal from the NUC* → check Part 10 and `journalctl -u heron-payload -f`.
4. **Record** → `REC 0:05`, MB/s showing → **Stop** → the new session appears at the top of *Recordings* in Payload Setup.
5. **Auto:** write the mission to the drone → **Auto** → start WP (default WP1), stop WP (default last) →
   **Arm auto-record** (strip turns amber). It records from arrival at the start WP to the stop WP, and
   stops early on RTL, land or disarm.

---

## Part 12 — Bench checklist before flying

- [ ] Unplug power, plug back: within ~1 min Payload Setup shows *Ready* with your setup.
- [ ] Strip shows *Payload ready* over the telemetry radio.
- [ ] 30 s Record/Stop from the planner works; Signal check good on every channel.
- [ ] Props off, armed on the bench: Auto armed → AUTO mode → recording starts at WP1 → disarm stops it.
- [ ] 15 min recording with the flight setup (Signal check → **15 min** → Record test): **Dropped stays 0**.
- [ ] Unplug the telemetry radio mid‑recording: the NUC keeps recording; the strip recovers when back.

## Field day
Cable laptop ↔ NUC, browser `http://192.168.137.10:8080` to check the setup,
then fly from the planner.

## Look at recordings  [Windows browser]
Payload Setup → **Recordings** (bottom of the page) lists every session on the data drive and the NUC SSD,
newest first, with setup, length, size and any dropped samples. Pick one, then per channel:
* **Spectrogram + histogram** draws both from a few hundred short slices spread across the recording
  (about 1 MB is read, so it is quick even for a long flight). Set *Start* / *Length* to zoom in on part of
  it; **Save pictures** downloads the PNGs.
* **Download…** saves one 30 s file (`.sc8`), plus `settings.json` / `samples.jsonl` per radio.
Viewing and downloading pause while the payload is recording or armed, so the disk keeps up.

## Copy whole sessions to Windows  [Windows]
Easiest: **WinSCP** (free) → New site → SFTP, host `192.168.137.10`, your NUC user → drag the
`HERON_…` folders from `/media/DataStore/heron/` (or `~/heron_recordings/`) to your laptop. Or:
```powershell
scp -r heron@192.168.137.10:/media/DataStore/heron/HERON_20261101_* $HOME\HERON_data\
```
(Change the date part to the session you want; recordings are large: ~2.6 GB per minute per channel.)

## Update the payload software later  [Windows]
No internet needed. Copy the new folder over (it merges into the old one), then run the updater:
```powershell
scp -r $HOME\HERON_Payload heron@192.168.137.10:~/
ssh -t heron@192.168.137.10 "sudo bash HERON_Payload/deploy/update.sh"
```
It copies the code to `/opt/heron`, rebuilds the recorder only if its source changed, restarts the service
and prints the new version (also shown next to the NUC's name at the top of Payload Setup). Your setups,
`/etc/heron/payload.env` and all recordings are kept. It refuses to run while the payload is recording.

## Handy NUC commands
| What | Command |
|---|---|
| Live log | `journalctl -u heron-payload -f` |
| Restart | `sudo systemctl restart heron-payload` |
| Free the radios for UHD tools | `sudo systemctl stop heron-payload` (start again with `start`) |
| Disk space | `df -h /media/DataStore ~` |
| Is the data drive mounted? | `findmnt /media/DataStore` (nothing printed = not mounted) |
| Radios | `uhd_find_devices` |
| Serial adapters | `ls -l /dev/serial/by-id/` |
| Shut down safely | `sudo poweroff` |
