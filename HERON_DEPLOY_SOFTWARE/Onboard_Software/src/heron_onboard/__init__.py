"""heron_onboard: the payload supervisor that runs on the NUC in flight.

Modules (REQUIREMENTS S2, one job each):

- ``config``: the onboard config model (all tunable values).
- ``state_machine``: pure decision logic: commands and fallback ->
  start/stop actions. No I/O, fully unit tested.
- ``capture``: start and watch the C++ recorder processes, one per SDR,
  and write the flight metadata.
- ``disk_monitor``: free-space checks and the stop-before-full rule.
- ``health``: CPU load, temperatures, uptime.
- ``supervisor``: the main loop that joins the parts and the link.
- ``cli``: the ``heron-onboard`` command.
"""
