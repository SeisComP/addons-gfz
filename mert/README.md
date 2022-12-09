# SeisComP MeRT repository

This repository contains the real-time energy magnitude (Me) module for SeisComP.

It is a joint effort of GFZ Sections 2.6 (Seismic Hazard and Risk Dynamics) and
2.4 (Seismology) implementing and using the methodology from Di Giacomo, D.,
Grosser, H., Parolai, S., Bormann, P., and Wang, R. (2008), Rapid determination
of Me for strong to great shallow earthquakes, Geophys. Res. Lett., 35, L10308,
doi:10.1029/2008GL033505

The repository cannot be built standalone. It needs to be integrated
into the `seiscomp` build environment and checked out into
`src/extras/mert`.

```
$ git clone [host]/seiscomp.git
$ cd seiscomp/src
$ git clone [host]/addons-gfz.git
$ ln -s ../addons-gfz/mert extras
```

# Build

## Compilation

Follow the build instructions from the `seiscomp` repository.
