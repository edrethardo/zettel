"""Eine Zeile je Frage, am Ende eine Zahl und ein Rückgabewert.

Geteilt von `smoke.py` (läuft der Shop?) und `veroeffentlichung.py` (darf das
Repo raus?). Die Klasse stand bis 2026-09-09 in `smoke.py`; als das zweite
Gate dazukam, wäre die Alternative eine Kopie gewesen — und zwei Kopien
derselben Ausgabe laufen auseinander, sobald jemand eine davon hübscher
macht. Der Inhalt ist unverändert, nur der Ort ist neu.
"""
from __future__ import annotations


class Bericht:
    """Eine Zeile je Frage. Am Ende eine Zahl und ein Rückgabewert."""

    def __init__(self, schluss: str = "") -> None:
        #: Was in der grünen Schlusszeile hinter der Zahl steht. `smoke.py`
        #: sagt dort, wovon es unabhängig ist; das Veröffentlichungs-Gate
        #: sagt, was es geprüft hat.
        self.schluss = schluss
        self.gruen = 0
        self.rot: list[str] = []

    def abschnitt(self, titel: str) -> None:
        print(f"\n-- {titel}")

    def ok(self, satz: str, beleg: str = "") -> None:
        self.gruen += 1
        print(f"[ok  ] {satz}" + (f" -- {beleg}" if beleg else ""))

    def fehler(self, satz: str, grund: str) -> None:
        self.rot.append(satz)
        print(f"[FAIL] {satz} -- {grund}")

    def pruefe(self, satz: str, fn) -> None:
        """Führt `fn` aus. Rückgabe ist der Beleg, eine Ausnahme der Grund."""
        try:
            beleg = fn()
        except Exception as e:  # noqa: BLE001 — der Grund gehört in die Zeile
            self.fehler(satz, f"{e.__class__.__name__}: {e}")
        else:
            self.ok(satz, "" if beleg is None else str(beleg))

    def ende(self) -> int:
        print()
        if self.rot:
            print(f"{len(self.rot)} von {self.gruen + len(self.rot)} Checks "
                  "rot:")
            for satz in self.rot:
                print(f"  - {satz}")
            return 1
        print(f"alle {self.gruen} Checks grün"
              + (f" -- {self.schluss}" if self.schluss else ""))
        return 0
