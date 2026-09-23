# Specular planner

Local map: Boulder Reservoir bounce points for every GNSS that shares GPS L5
(1176.45 MHz): GPS L5, Galileo E5a, BeiDou-3 B2a, QZSS L5.

## Run

```bash
cd HERON/Specular_Planner
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py
```

Open http://127.0.0.1:5055

Refresh orbits (optional):

```bash
.venv/bin/python data/fetch_gnss.py
```

Almanacs are for **planning** (splash on this cove). After a flight, use that day's broadcast `.nav` for height math.

