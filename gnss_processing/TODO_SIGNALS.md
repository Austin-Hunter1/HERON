



Signal Family is not a term used to describe the different GNSS links.  Rather, the term "link" is used.

Signal Link:  L1, L2, L5, E1, E5, etc.

Signal:  A particular signal definition on a given link for a given constellation.

A signal has a carrier frequency and one or more spreading code components.

The carrier frequency can correspond to the link frequency for simple modulations.  When considering a single sidelobe of a BOC modulation, the signal carrier frequency may be different from the link frequency (e.g. Galileo E5a vs E5b vs E5).

A spreading code component can be a simple BPSK spreading code, a TDBPSK component (e.g. L2CM and L2CL), a BOC component, or QPSK component (e.g. L5I and L5Q).  Note: GPS uses TMBOC and Galileo uses CBOC.  Many signals can use a simple BPSK correlator implementation (e.g. GPS L1CA and GPS L5 -- each component of the QPSK signal should simply use the BPSK correlator).  We should use specialized correlator implementations for the L2C TD-BPSK, the GPS L1C TMBOC, and the Galileo CBOC signals, which accounts for the code multiplexing or the specialized subcarrier structure.

A signal has a type and 1 or more spreading code components.
TMBOC and MBOC split power between two BOC subcarriers.
Code components can additionally have zero or more overlays defined.
The overlays can be a data sequence (random symbols) or a predefined overlay sequence (e.g. Weil code, Legendre sequence, etc).

Signal definitions should be simple to understand, but complete.
Acquisition policies and strategies should be definied separately from signal definition.  The acquisition typically is performed using a single code component.
Discriminator policies and tracking loop parameters should be defined separately from the signal definition.
