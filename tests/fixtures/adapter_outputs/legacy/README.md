# Legacy adapter-output fixtures

Fixtures in this folder were tolerated before the strict structured-output
contract landed. Each one carries a key the schema no longer accepts.

They are kept as regression fixtures: the migration test asserts that every
file here is rejected under strict mode, proving the strict contract is what
moved them out of the main fixture set. They are never parsed by production
code.

The main fixture set (the parent folder) must parse cleanly under strict
mode. Add a new strict-clean fixture there; add a previously-tolerated payload
here.
