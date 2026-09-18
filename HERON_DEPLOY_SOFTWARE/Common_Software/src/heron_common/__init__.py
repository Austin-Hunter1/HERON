"""heron_common: code shared by the onboard and base station software.

This package holds the parts that both sides of the payload link must
agree on:

- ``config``: load and validate TOML config files.
- ``protocol``: the command and telemetry messages, the frame format,
  and the transports that carry frames.
- ``logging_setup``: one logging setup for all HERON programs.
- ``version``: the git hash of the running code.
- ``timeutil``: UTC helpers.

Keep this package small. Put onboard-only or base-only code in its own
package.
"""
