# Changelog

## Eighth review

A blind recall after the seventh review reproduced two defects in how an address is read, and the docs gave an old mutation figure.

- A unit written in round brackets is now read as the unit. `30 Smith St (Unit 5)`, `(Unit 6) 30 Smith St` and `30 Smith St (Apt 15)` used to lose the unit as a note, so every unit of a tower linked to the building's own address at score 1.0. They now match the unit's own address, as `30 Smith St [Unit 6]` and `30 Smith St, Unit 5` already did. A bracket with no unit word, such as `(rear)` or `(5)`, is still a dropped note, and a unit the building does not have is still set aside as a unit conflict.
- A trailing `Home`, `Office`, `Shop`, `Garage`, `Penthouse` or `Granny Flat` after a street type no longer turns St into Saint. `12 Smith St Home, Fakeville NSW 2000` used to read as `Smith Saint Home` and mint a second property beside the real address; the word is now dropped (rule DESCRIPTOR_AFTER_STREET) and the address links to the real one.
- The README now gives the same mutation figure as `docs/mutation-results.txt` and `docs/tests.md`: 1,998 mutants after the seventh review, not 1,798 after the fifth. The two fixes above came after that run and are pinned by `tests/test_recall8_fixes.py`; they are not mutation checked.
