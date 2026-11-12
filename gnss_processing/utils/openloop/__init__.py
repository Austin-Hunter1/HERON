"""
Open-loop (OL) tracking of reflected GNSS signals (notebook 03).

The plan is `docs/NB03_OPEN_LOOP_PLAN.md`. The decisions are D-087 to D-101 in
`../docs/DECISIONS.md`. The known gaps and risks are in
`docs/NB03_CONCERNS.md`.

The package is split into small modules so that one model can change without a
change to the others:

- `frames`       -- coordinate frames and rotations (ECEF, geodetic, NED, body).
- `config`       -- load and validate `configs/openloop.yml`.
- `timebase`     -- sample time ("uptime") to GPS time.
- `trajectory`   -- receiver trajectory input (RTKLIB .pos, static point).
- `attitude`     -- drone attitude input (ArduPilot log export, constant).
- `antennas`     -- lever arms: from the trajectory point to each antenna.
- `sat_states`   -- satellite position, velocity and clock (broadcast, precise).
- `surface`      -- reflecting surface: ellipsoid, geoid, water level, normal.
- `specular`     -- specular point (flat image method, curved iterative method).
- `atmosphere`   -- ionosphere and troposphere, absolute and excess terms.
- `reflectivity` -- Fresnel coefficients and the coherent link budget.
- `direct_ref`   -- the direct-signal reference from a notebook 01 tracking run.
- `model`        -- the OL model: delay, Doppler and phase of the reflection.
- `correlator`   -- the OL correlator loop (complex waveforms).
- `acquisition`  -- the small delay-Doppler map around the model.
- `phase`        -- data wipe, phase unwrapping, coherence, SCANF filter.
- `leakage`      -- the direct signal in the nadir antenna (estimate only, D-098).
- `heights`      -- height above the surface from the excess path.
- `io`           -- save and load `openloop/*.h5`.
"""
