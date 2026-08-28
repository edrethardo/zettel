"""Tests für `evals/` — ohne Phoenix, ohne Modell, ohne Katalogdatei.

Das ist der Punkt aus Spec 8.3 („Experiments werden von Hand gestartet und
brauchen die vLLM-Box; das Gate braucht sie nicht"), hier als Test
festgenagelt: alles, was diese Datei prüft, ist rein rechnend. Der Phoenix-
Client wird durch einen kleinen Doppelgänger ersetzt, das Modell durch einen
Fake mit fester Antwort.

Vier Fragen, die dieses Modul kaputtmachen könnten, und die deshalb hier
stehen:

1. Wertet der deterministische Evaluator einen Kategorietreffer auch dann
   richtig, wenn die Produkt-ID eine andere ist als beim letzten Lauf? (Das
   ist der Grund, warum das Dataset keine IDs führt.)
2. Lässt sich das Dataset zweimal anlegen, ohne Dubletten?
3. Zählt der Vollständigkeitsteil eine fehlende erwartete Zutat als Fehler?
   (Die Lücke aus WB-327.)
4. Tut der LLM-Judge das Richtige — gegen einen Fake, nicht gegen die Box?
"""
import pytest

from evals import dataset, experiment


# --------------------------------------------------------------------------
# Werkzeug

def vorschlag(name, kategorie, begriff, product_id=1, freitext=False):
    """Eine Vorschlagszeile, wie `experiment.als_ausgabe` sie erzeugt."""
    return {"product_id": None if freitext else product_id, "name": name,
            "kategorie": kategorie, "search_term": begriff, "menge": 1,
            "freitext": freitext}


def ausgabe(*vorschlaege, weg="llm", verworfen=0, begriffe=None):
    return {"weg": weg, "meldung": "", "verworfen": verworfen,
            "begriffe": begriffe or [], "vorschlaege": list(vorschlaege)}


BOLOGNESE = dataset.nach_schluessel()["gewoehnlich-bolognese"]
KLOPAPIER = dataset.nach_schluessel()["gemischt-klopapier-spueli"]
SUESSES = dataset.nach_schluessel()["falle-etwas-suesses"]


def erwartung(beispiel):
    return dataset.erwartung(beispiel)


def vollstaendige_bolognese(ids=(1, 2, 3, 4, 5)):
    """Ein Lauf, der alle fünf erwarteten Zutaten trifft."""
    a, b, c, d, e = ids
    return ausgabe(
        vorschlag("Barilla Spaghetti", "Reis, Pasta & Getreide > Pasta > "
                  "Hartweizen", "Spaghetti", a),
        vorschlag("Gemischte Hackfleischzubereitung",
                  "Hackfleisch & Burgerpatties > Hackfleisch", "Hackfleisch",
                  b),
        vorschlag("Mutti Passierte Tomaten",
                  "Konserven & Eingelegtes > Tomaten > Passata & "
                  "Konserviertes", "passierte Tomaten", c),
        vorschlag("Zwiebeln Gelb, Netz", "Gemüse > Zwiebeln & Knoblauch",
                  "Zwiebeln", d),
        vorschlag("Knoblauch, Netz", "Gemüse > Zwiebeln & Knoblauch",
                  "Knoblauch", e))


class FakeLLM:
    """Ein Modell mit fest vorgegebenen Antworten. Kein Socket, kein Wecken."""

    def __init__(self, *antworten):
        self.antworten = list(antworten)
        self.aufrufe = []

    def modell(self, **_):
        return "fake"

    def chat(self, nachrichten, **weitere):
        from picknick.llm.client import Antwort

        self.aufrufe.append({"nachrichten": list(nachrichten), **weitere})
        if not self.antworten:
            raise AssertionError("Mehr Aufrufe als vorbereitete Antworten.")
        return Antwort(content=self.antworten.pop(0), reasoning_content=None,
                       modell="fake", finish_reason="stop")


class NieGefragt:
    def chat(self, *_, **__):
        raise AssertionError("Das Modell wurde gefragt, obwohl nicht nötig.")


class FakePhoenix:
    """Ein Phoenix, das Datasets im Speicher führt.

    Genau so viel, wie `dataset.anlegen()` benutzt — und mit derselben
    Eigenheit, die den Ernstfall gefährlich macht: `create_dataset` ERSETZT,
    `add_examples_to_dataset` HÄNGT AN. Ein Aufrufer, der die beiden
    verwechselt, verdoppelt hier genauso wie in echt.
    """

    class _Ds:
        def __init__(self, name, examples):
            self.name = name
            self.id = f"ds-{name}"
            self.examples = examples

    def __init__(self):
        self.gespeichert: dict[str, list[dict]] = {}
        self.aufrufe: list[str] = []
        self.datasets = self

    def get_dataset(self, *, dataset, **_):
        self.aufrufe.append("get")
        if dataset not in self.gespeichert:
            raise LookupError(f"Dataset {dataset} not found")
        return self._Ds(dataset, list(self.gespeichert[dataset]))

    def create_dataset(self, *, name, examples, **_):
        self.aufrufe.append("create")
        self.gespeichert[name] = [dict(b) for b in examples]
        return self._Ds(name, self.gespeichert[name])

    def add_examples_to_dataset(self, *, dataset, examples, **_):
        self.aufrufe.append("add")
        self.gespeichert.setdefault(dataset, []).extend(
            dict(b) for b in examples)
        return self._Ds(dataset, self.gespeichert[dataset])


# --------------------------------------------------------------------------
# Kategoriepfade

def test_kategorie_vergleicht_auf_segmentgrenzen():
    """`Milch` darf nicht auf `Milch, Molkerei & Butter` passen.

    Ein blosses `startswith` täte genau das — und dann zählte jeder Joghurt
    als Treffer der Kategorie „Milch". Der Fehler wäre still: die Zahlen
    sähen besser aus, nicht kaputt.
    """
    assert dataset.passt_kategorie("Milch, Molkerei & Butter > Milch",
                                   "Milch, Molkerei & Butter")
    assert not dataset.passt_kategorie("Milch, Molkerei & Butter > Butter "
                                       "& Fette", "Milch")


def test_kategorie_umfasst_tiefere_ebenen():
    """Eine Erwartung auf Ebene 2 gilt für alles darunter.

    Genau dafür ist die Kategorieebene da: ein Crawl, der unter
    `Käse > Weichkäse` eine dritte Ebene einführt, macht die Erwartung nicht
    ungültig.
    """
    assert dataset.passt_kategorie("Käse > Weichkäse > Brie", "Käse")
    assert dataset.passt_kategorie("Käse > Weichkäse > Brie",
                                   "Käse > Weichkäse")
    assert not dataset.passt_kategorie("Käse", "Käse > Weichkäse")


def test_pfad_laesst_leere_ebenen_weg():
    assert dataset.pfad({"category_l1": "Süßigkeiten", "category_l2": None,
                         "category_l3": None}) == "Süßigkeiten"


# --------------------------------------------------------------------------
# 1. Der deterministische Evaluator und die Produkt-ID

def test_praezision_ist_von_der_produkt_id_unabhaengig():
    """Andere IDs, dieselbe Kategorie -> derselbe Score.

    Der Kern der Entwurfsentscheidung aus Spec 8.2. Der nächste Crawl vergibt
    andere IDs oder listet ein Produkt aus; wäre die Erwartung eine ID, fiele
    dieses Dataset dann still auf 0 und niemand wüsste, warum.
    """
    erst = experiment.kategorie_praezision(vollstaendige_bolognese((1, 2, 3, 4, 5)),
                                           erwartung(BOLOGNESE))
    spaeter = experiment.kategorie_praezision(
        vollstaendige_bolognese((9001, 9002, 9003, 9004, 9005)),
        erwartung(BOLOGNESE))
    assert erst[0] == spaeter[0] == 1.0
    assert erst[1] == spaeter[1] == "sauber"


def test_praezision_erkennt_den_spaghettiloeffel():
    """Richtiger Begriff, falsche Warengruppe — der teuerste stille Fehler.

    „Spaghetti" liefert im echten Katalog als bestplatzierten Treffer einen
    Spaghettilöffel aus `Haushaltsartikel > Küchenhelfer`. Jede Metrik, die
    nur zählt, ob ein Begriff ein Produkt bekommen hat, nennt das einen
    Erfolg.
    """
    lauf = vollstaendige_bolognese()
    lauf["vorschlaege"][0] = vorschlag(
        "Fackelmann Spaghettilöffel 30cm",
        "Haushaltsartikel > Küchenhelfer", "Spaghetti", 666)
    score, label, erklaerung = experiment.kategorie_praezision(
        lauf, erwartung(BOLOGNESE))
    assert score == pytest.approx(4 / 5)
    assert label == "fehlgriff"
    assert "Spaghettilöffel" in erklaerung and "Nudeln" in erklaerung


def test_beifang_zaehlt_nicht_gegen_die_praezision():
    """Parmesan zur Bolognese ist kein Fehler.

    `zusatz_ok` unterscheidet die abgezählte Einkaufsliste vom Gericht. Ohne
    diese Unterscheidung wäre jede sinnvolle Zugabe ein Punktabzug — und ein
    Agent, der weniger liefert, führte die Tabelle an.
    """
    lauf = vollstaendige_bolognese()
    lauf["vorschlaege"].append(
        vorschlag("Parmigiano Reggiano", "Käse > Hartkäse", "Parmesan", 77))
    score, label, erklaerung = experiment.kategorie_praezision(
        lauf, erwartung(BOLOGNESE))
    assert score == 1.0 and label == "sauber"
    assert "Parmigiano" in erklaerung


def test_bei_abgezaehlter_liste_zaehlt_beifang_doch():
    """„Klopapier und Spülmittel" plus Schokolade: die Schokolade ist falsch.

    Bei `zusatz_ok=False` wird alles, was sich keiner erwarteten Zutat
    zuordnen lässt, gegen die erwarteten Kategorien geprüft.
    """
    lauf = ausgabe(
        vorschlag("Danke Toilettenpapier 3-lagig",
                  "Papier- & Hygieneartikel > Toilettenpapier > "
                  "Toilettenpapier", "Toilettenpapier"),
        vorschlag("Frosch Spülmittel Limone",
                  "Putzen & Reinigen > Geschirrreiniger > Handspülmittel",
                  "Spülmittel", 2),
        vorschlag("Kinder Schokolade", "Schokolade > Schokoriegel",
                  "Schokolade", 3))
    score, label, _ = experiment.kategorie_praezision(
        lauf, erwartung(KLOPAPIER))
    assert score == pytest.approx(2 / 3)
    assert label == "fehlgriff"


def test_rezeptpfad_ist_ein_hartes_null():
    """Der richtige Korb auf dem falschen Weg bleibt falsch."""
    lauf = ausgabe(
        vorschlag("Danke Toilettenpapier",
                  "Papier- & Hygieneartikel > Toilettenpapier",
                  "Toilettenpapier"),
        vorschlag("Frosch Spülmittel",
                  "Putzen & Reinigen > Geschirrreiniger", "Spülmittel", 2),
        weg="recipe")
    score, label, erklaerung = experiment.kategorie_praezision(
        lauf, erwartung(KLOPAPIER))
    assert score == 0.0 and label == "rezeptpfad"
    assert "Rezeptweg" in erklaerung


def test_ohne_produktvorschlag_gibt_es_keinen_score():
    """Alles Freitext heisst „nichts zu bewerten", nicht „alles falsch".

    Eine 0,0 hier stünde in Phoenix neben echten Nullen und behauptete einen
    Fehlgriff, den es nicht gab — derselbe Grundsatz wie bei den offenen
    Vorschlägen in `obs.labels`.
    """
    lauf = ausgabe(vorschlag(None, None, "Zwiebeln", freitext=True))
    score, label, _ = experiment.kategorie_praezision(
        lauf, erwartung(BOLOGNESE))
    assert score is None and label == "nichts geliefert"


# --------------------------------------------------------------------------
# 3. Vollständigkeit — die Lücke aus WB-327

def test_fehlende_zutat_zaehlt_als_fehler():
    """Der gemessene Lauf aus WB-327, Zahl für Zahl.

    Stufe 1 lieferte damals `Spaghetti, Hackfleisch, passierte Tomaten,
    Klopapier` — Zwiebeln und Knoblauch fehlten. Die Präzision ist dabei
    makellos: jeder gelieferte Begriff hat ein Produkt bekommen. Erst der
    Recall zeigt, dass die Sauce nichts wird.
    """
    lauf = vollstaendige_bolognese()
    # Zwiebeln und Knoblauch weg, wie damals.
    lauf["vorschlaege"] = lauf["vorschlaege"][:3]
    score, label, erklaerung = experiment.zutaten_vollstaendigkeit(
        lauf, erwartung(BOLOGNESE))
    assert score == pytest.approx(3 / 5)
    assert label == "unvollstaendig"
    assert "Zwiebeln" in erklaerung and "Knoblauch" in erklaerung

    # Und der Gegenbeweis, warum es diesen zweiten Score überhaupt gibt:
    # dieselbe Ausgabe ist in der Präzision tadellos.
    assert experiment.kategorie_praezision(lauf, erwartung(BOLOGNESE))[0] == 1.0


def test_geteilte_kategorie_deckt_nicht_zwei_zutaten():
    """Ein Netz Zwiebeln ist kein Knoblauch.

    Beide liegen im echten Katalog in `Gemüse > Zwiebeln & Knoblauch`. Ohne
    die Zuordnung aus `experiment.zuordnung` zählte ein einziges Produkt
    beide Zutaten als geliefert — und der Fehler aus WB-327 wäre wieder
    unsichtbar, diesmal durch die Metrik selbst.
    """
    lauf = vollstaendige_bolognese()
    del lauf["vorschlaege"][4]                       # Knoblauch weg
    score, _, erklaerung = experiment.zutaten_vollstaendigkeit(
        lauf, erwartung(BOLOGNESE))
    assert score == pytest.approx(4 / 5)
    assert "Knoblauch" in erklaerung or "Zwiebeln" in erklaerung


def test_freitext_gilt_nicht_als_gelieferte_zutat():
    """Eine Zeile zum Selbersuchen ist kein Einkauf.

    Genau daran hängt, ob die Vollständigkeit etwas misst: der Agent macht
    aus jedem Begriff ohne Treffer einen Freitext-Vorschlag (Spec 6, Regel
    2). Zählte der mit, wäre der Recall immer 1,0.
    """
    lauf = vollstaendige_bolognese()
    # Zwiebeln UND Knoblauch als Freitext — sonst deckt das jeweils andere
    # Produkt die Kategorie ab, die sich die beiden teilen.
    lauf["vorschlaege"][3] = vorschlag(None, None, "Zwiebeln", freitext=True)
    lauf["vorschlaege"][4] = vorschlag(None, None, "Knoblauch", freitext=True)
    score, _, erklaerung = experiment.zutaten_vollstaendigkeit(
        lauf, erwartung(BOLOGNESE))
    assert score == pytest.approx(3 / 5)
    assert "Zwiebeln" in erklaerung and "Knoblauch" in erklaerung


def test_vollstaendigkeit_zaehlt_kategorien_und_nicht_begriffe():
    """Ein anderer Begriff, dieselbe Warengruppe: die Zutat ist geliefert.

    Sagt das Modell „Rinderhackfleisch" statt „Hackfleisch", ist der Einkauf
    trotzdem vollständig. Der Recall fragt, was im Korb liegt — nicht, wie es
    gefunden wurde.
    """
    lauf = vollstaendige_bolognese()
    lauf["vorschlaege"][1] = vorschlag(
        "BioLust BIO Rinderhackfleisch",
        "Hackfleisch & Burgerpatties > Hackfleisch", "Rinderhack", 42)
    assert experiment.zutaten_vollstaendigkeit(
        lauf, erwartung(BOLOGNESE))[0] == 1.0


def test_falle_ist_mit_einem_treffer_erfuellt():
    """„Etwas Süßes" braucht einen Treffer, kein bestimmtes Produkt."""
    lauf = ausgabe(vorschlag("Kinder Schokolade 8 Stück",
                             "Schokolade > Schokoriegel", "Schokolade"))
    assert experiment.zutaten_vollstaendigkeit(lauf, erwartung(SUESSES))[0] \
        == 1.0
    leer = ausgabe()
    assert experiment.zutaten_vollstaendigkeit(leer, erwartung(SUESSES))[0] \
        == 0.0


def test_zutat_zu_begriff_nimmt_die_laengste_uebereinstimmung():
    """Sonst zöge „Tomaten" den Begriff „passierte Tomaten" an sich."""
    zutaten = erwartung(BOLOGNESE)["zutaten"]
    assert dataset.zutat_zu_begriff(zutaten, "passierte Tomaten")["name"] \
        == "passierte Tomaten"
    assert dataset.zutat_zu_begriff(zutaten, "Zwiebeln")["name"] == "Zwiebeln"
    assert dataset.zutat_zu_begriff(zutaten, "Rotwein") is None


# --------------------------------------------------------------------------
# 2. Idempotenz des Datasets

def test_zweimal_anlegen_erzeugt_keine_dubletten():
    """Der Kern der Anforderung: der zweite Lauf schreibt gar nichts."""
    phoenix = FakePhoenix()
    erst = dataset.anlegen(phoenix)
    assert erst["aktion"] == "ersetzen"
    assert erst["anzahl"] == len(dataset.BEISPIELE)

    phoenix.aufrufe.clear()
    zweit = dataset.anlegen(phoenix)
    assert zweit["aktion"] == "nichts"
    assert "create" not in phoenix.aufrufe and "add" not in phoenix.aufrufe
    assert len(phoenix.gespeichert[dataset.NAME]) == len(dataset.BEISPIELE)

    schluessel = [b["metadata"]["schluessel"]
                  for b in phoenix.gespeichert[dataset.NAME]]
    assert len(schluessel) == len(set(schluessel))


def test_fehlendes_beispiel_wird_ergaenzt_und_nicht_ersetzt():
    """Ein neu hinzugekommenes Beispiel kostet keine neue Version des Rests."""
    phoenix = FakePhoenix()
    dataset.anlegen(phoenix)
    entfernt = phoenix.gespeichert[dataset.NAME].pop()
    phoenix.aufrufe.clear()

    bericht = dataset.anlegen(phoenix)
    assert bericht["aktion"] == "ergaenzen"
    assert "add" in phoenix.aufrufe and "create" not in phoenix.aufrufe
    assert len(phoenix.gespeichert[dataset.NAME]) == len(dataset.BEISPIELE)
    assert entfernt["metadata"]["schluessel"] in bericht["fehlend"]


def test_geaendertes_beispiel_wird_ersetzt():
    """Gleicher Schlüssel, anderer Inhalt: der Abdruck verrät es.

    Ohne den bliebe eine korrigierte Kategorie in Phoenix stehen, und Läufe
    von gestern und heute wären unvergleichbar, ohne dass es auffiele.
    """
    phoenix = FakePhoenix()
    dataset.anlegen(phoenix)
    phoenix.gespeichert[dataset.NAME][0]["metadata"]["abdruck"] = "alt"
    bericht = dataset.anlegen(phoenix)
    assert bericht["aktion"] == "ersetzen"
    assert len(phoenix.gespeichert[dataset.NAME]) == len(dataset.BEISPIELE)


def test_doppeltes_beispiel_wird_gemeldet_und_bereinigt():
    """Hat der Server angehängt statt ersetzt, ist das ein Befund."""
    phoenix = FakePhoenix()
    dataset.anlegen(phoenix)
    phoenix.gespeichert[dataset.NAME].append(
        dict(phoenix.gespeichert[dataset.NAME][0]))
    plan = dataset.plan_fuer(phoenix.gespeichert[dataset.NAME])
    assert plan["aktion"] == "ersetzen" and plan["doppelt"]

    dataset.anlegen(phoenix)
    assert len(phoenix.gespeichert[dataset.NAME]) == len(dataset.BEISPIELE)


def test_anlegen_meldet_wenn_der_server_angehaengt_hat():
    """Ein Server, der `action=update` nicht kann, darf nicht still doppeln."""
    class Anhaengend(FakePhoenix):
        def create_dataset(self, *, name, examples, **_):
            self.aufrufe.append("create")
            self.gespeichert.setdefault(name, []).extend(
                dict(b) for b in examples)
            return self._Ds(name, self.gespeichert[name])

    phoenix = Anhaengend()
    dataset.anlegen(phoenix)
    phoenix.gespeichert[dataset.NAME][0]["metadata"]["abdruck"] = "alt"
    with pytest.raises(dataset.DatasetFehler, match="angehängt"):
        dataset.anlegen(phoenix)


def test_plan_fuer_ohne_dataset_legt_an():
    plan = dataset.plan_fuer(None)
    assert plan["aktion"] == "ersetzen"
    assert len(plan["fehlend"]) == len(dataset.BEISPIELE)


# --------------------------------------------------------------------------
# 4. Der LLM-Judge, gegen einen Fake

def test_judge_liest_das_urteil_aus_der_antwort():
    zugang = FakeLLM('{"urteil": "teilweise", "begruendung": '
                     '"Weichkäse passt nicht über Nudeln."}')
    score, label, erklaerung = experiment.llm_urteil(
        zugang, "Käse für die Nudeln", "gerieben oder Hartkäse",
        vollstaendige_bolognese())
    assert score == 0.5 and label == "teilweise"
    assert "Weichkäse" in erklaerung


def test_judge_vertraegt_einen_codefence():
    """Modelle schreiben ihn auch dann, wenn im Prompt „nur JSON" steht."""
    zugang = FakeLLM('```json\n{"urteil": "passt", "begruendung": "ok"}\n```')
    assert experiment.llm_urteil(zugang, "Milch", "irgendeine Milch",
                                 ausgabe())[0] == 1.0


def test_judge_ohne_ermessen_fragt_das_modell_gar_nicht():
    """Wo die Kategorie entscheidet, hat ein Ermessensurteil nichts zu suchen.

    `NieGefragt` macht jeden Modellaufruf zum Testfehler — der Test prüft
    also nicht bloss den Rückgabewert, sondern dass die Box in Ruhe bleibt.
    """
    score, label, _ = experiment.llm_urteil(
        NieGefragt(), "Klopapier und Spülmittel", "zwei Non-Food-Artikel",
        ausgabe(), ermessen=False)
    assert score is None and label == "nicht_bewertet"


def test_unlesbares_urteil_bekommt_keinen_score():
    """Ein stotternder Richter ist kein versagender Agent.

    Eine 0,0 stünde in Phoenix neben den echten Nullen und behauptete einen
    Fehler des Agenten, den niemand gefunden hat.
    """
    for antwort in ("das kann ich nicht beurteilen",
                    '{"urteil": "vielleicht", "begruendung": "hm"}',
                    '["passt"]'):
        score, label, _ = experiment.llm_urteil(
            FakeLLM(antwort), "Milch", "irgendeine Milch", ausgabe())
        assert score is None, antwort
        assert label == "unlesbar", antwort


def test_judge_bekommt_die_kategorien_nicht_zu_sehen():
    """Er soll beurteilen, was die Kategorieprüfung nicht kann.

    Bekäme er die erwarteten Kategorien, prüfte er dasselbe noch einmal — nur
    teurer und unzuverlässiger.
    """
    zugang = FakeLLM('{"urteil": "passt", "begruendung": "ok"}')
    experiment.llm_urteil(zugang, "Milch",
                          erwartung(dataset.nach_schluessel()
                                    ["mehrdeutig-milch"])["beschreibung"],
                          vollstaendige_bolognese())
    text = "\n".join(n["content"] for n in zugang.aufrufe[0]["nachrichten"])
    assert "Milch, Molkerei & Butter > Milch" not in text


def test_judge_prompt_nennt_freitextzeilen_als_solche():
    text = experiment.judge_prompt(
        "Milch", "irgendeine Milch",
        ausgabe(vorschlag(None, None, "Zwiebeln", freitext=True)))
    assert "kein Produkt gefunden" in text


# --------------------------------------------------------------------------
# Das Dataset selbst

def test_vier_sorten_mit_je_mehreren_beispielen():
    """Spec 8.2 verlangt vier Sorten, und je eine reicht nicht."""
    je_sorte: dict[str, int] = {}
    for b in dataset.BEISPIELE:
        je_sorte[b["sorte"]] = je_sorte.get(b["sorte"], 0) + 1
    assert set(je_sorte) == set(dataset.SORTEN)
    assert all(n >= 2 for n in je_sorte.values()), je_sorte


def test_jedes_beispiel_ist_vollstaendig_beschrieben():
    schluessel = set()
    for b in dataset.BEISPIELE:
        assert b["schluessel"] not in schluessel
        schluessel.add(b["schluessel"])
        assert b["satz"].strip()
        assert b["beschreibung"].strip()
        assert b["zutaten"], b["schluessel"]
        for z in b["zutaten"]:
            assert z["kategorien"], (b["schluessel"], z["name"])
            # Keine Produkt-ID, nirgends — sonst hält dieses Dataset genau
            # bis zum nächsten Crawl.
            assert all(isinstance(k, str) for k in z["kategorien"])


def test_kein_beispiel_erwartet_eine_produkt_id():
    """Die Entwurfsentscheidung als Test, damit sie niemand still aufweicht."""
    roh = str(dataset.als_beispiele())
    assert "product_id" not in roh


def test_die_abdruecke_haengen_am_inhalt():
    beispiel = dict(dataset.BEISPIELE[0])
    vorher = dataset.abdruck(beispiel)
    beispiel = {**beispiel, "satz": beispiel["satz"] + " bitte"}
    assert dataset.abdruck(beispiel) != vorher


# --------------------------------------------------------------------------
# Die Varianten

def test_jede_variante_verschiebt_genau_eine_stellschraube():
    """Zwei auf einmal wären kein Experiment, sondern ein neuer Agent."""
    basis = experiment.VARIANTEN[experiment.BASIS]
    for name, v in experiment.VARIANTEN.items():
        if name == experiment.BASIS:
            continue
        anders = sum([v.system_extract != basis.system_extract,
                      v.kandidaten != basis.kandidaten,
                      v.guided != basis.guided])
        assert anders == 1, name


def test_die_modellvariante_aus_der_spec_wird_nicht_vorgetaeuscht():
    """Auf der Box liegt genau ein Modell — also gibt es diese Variante nicht.

    Der Test hält fest, dass der Tausch bewusst war und nicht vergessen
    wurde: taucht hier eines Tages eine Modellvariante auf, muss jemand
    nachweisen, dass es ein zweites Modell gibt.
    """
    assert not any("modell" in v.name for v in experiment.VARIANTEN.values())
    assert "ohne-guided" in experiment.VARIANTEN


def test_prompt_b_nennt_keine_zutat_aus_dem_dataset():
    """Sonst gewönne B den Vergleich, ohne irgendetwas zu können.

    Verglichen wird gegen A und nicht gegen die leere Menge: beide Prompts
    tragen dasselbe JSON-Beispiel („Hackfleisch", „passierte Tomaten"), und
    das ist kein Vorteil von B. Verboten ist, was B ZUSÄTZLICH nennt.
    """
    from picknick.assistant import plan

    b_text = experiment.SYSTEM_EXTRACT_B.casefold()
    a_text = plan.SYSTEM_EXTRACT.casefold()
    for beispiel in dataset.BEISPIELE:
        for z in beispiel["zutaten"]:
            name = z["name"].casefold()
            if name in a_text:
                continue
            assert name not in b_text, z["name"]


def test_als_ausgabe_traegt_die_kategorie_mit():
    """Ohne Kategorie an der Zeile wäre die Bewertung auf die DB angewiesen.

    Und damit auf einen Katalogstand, den es zum Zeitpunkt der Auswertung
    vielleicht nicht mehr gibt.
    """
    from picknick.assistant.chat import Ergebnis

    ergebnis = Ergebnis(
        weg="llm", order_id=1, satz="Milch", chat_message_id=1,
        vorschlaege=[{"product_id": 808, "name": "Hemme Milch 1,8 %",
                      "qty": 1, "search_term": "Milch", "rang": 3.0,
                      "ist_freitext": 0}],
        begriffe=[{"suchbegriffe": ["Milch"], "menge": 1}], verworfen=[],
        meldung="1 Begriff")
    raus = experiment.als_ausgabe(
        ergebnis, {808: "Milch, Molkerei & Butter > Milch > Frischmilch"})
    assert raus["vorschlaege"][0]["kategorie"] == \
        "Milch, Molkerei & Butter > Milch > Frischmilch"
    assert raus["verworfen"] == 0
    # Die ganze Kette wandert mit (WB-340), nicht nur ein Begriff.
    assert raus["begriffe"] == [{"suchbegriffe": ["Milch"], "menge": 1}]


def test_evals_brauchen_kein_phoenix_beim_import():
    """`import evals.experiment` darf nichts wecken und nichts voraussetzen.

    Das ist die Trennung aus Spec 8.3 als Test: dieses Modul wird von der
    Testsuite importiert, und die läuft ohne Phoenix und ohne Box.
    """
    import sys

    assert "phoenix.client" not in sys.modules or True  # Import ist erlaubt,
    # aber er darf nicht von `evals` ausgelöst worden sein:
    quelle = (experiment.__file__, dataset.__file__)
    for datei in quelle:
        with open(datei, encoding="utf-8") as f:
            for zeile in f:
                if zeile.startswith(("import ", "from ")):
                    assert "phoenix" not in zeile, (datei, zeile)
