"""`scripts/hf_datensatz.py`: aus den Roh-JSONs der Breitenmessung zwei CSVs
für den Hugging-Face-Datensatz — `dishes.csv` (jedes Gericht einmal) und
`results.csv` (eine Zeile je Lauf und Gericht)."""
import csv
import importlib.util
import json
import pathlib

import pytest

SKRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "hf_datensatz.py"


@pytest.fixture
def hf():
    spec = importlib.util.spec_from_file_location("hf_datensatz", SKRIPT)
    modul = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modul)
    return modul


def _lauf(tmp_path, name, modell, zeilen):
    roh = tmp_path / f"breite_probe-{name}.json"
    roh.write_text(json.dumps(zeilen, ensure_ascii=False), encoding="utf-8")
    (tmp_path / f"breite_probe-{name}.provenienz.json").write_text(json.dumps({
        "modell": modell, "created_lesbar": "2026-09-05T08:05:00",
        "phoenix_projekt": f"Zettel Eval {modell}"}), encoding="utf-8")
    return roh


def _gericht(name, achse, begriffe, treffer, **rest):
    z = {"gericht": name, "achse": achse, "satz": f"alles für {name}", "rest": None,
         "weg": "chefkoch", "begriffe": begriffe, "katalogtreffer": treffer,
         "freitext": begriffe - treffer, "verworfen": 0, "dauer_s": 6.5, "fehler": None,
         "quelle_name": f"{name} klassisch"}
    z.update(rest)
    return z


def test_zwei_laeufe_ergeben_ein_gerichte_blatt_und_ein_ergebnis_blatt(hf, tmp_path):
    a = _lauf(tmp_path, "a", "Qwen", [_gericht("Rouladen", "Alltagsküche", 13, 12),
                                       _gericht("Pho Bo", "international, exotisch", 19, 11, rest="Zahnpasta")])
    b = _lauf(tmp_path, "b", "Nemotron", [_gericht("Rouladen", "Alltagsküche", 12, 12),
                                           _gericht("Pho Bo", "international, exotisch", 18, 12, rest="Zahnpasta")])
    ziel = tmp_path / "hf"
    hf.schreiben([a, b], ziel)

    gerichte = list(csv.DictReader((ziel / "dishes.csv").open(encoding="utf-8")))
    assert [g["dish"] for g in gerichte] == ["Rouladen", "Pho Bo"]      # jedes Gericht einmal
    assert gerichte[1]["axis"] == "international, exotisch"
    assert gerichte[1]["extra_article"] == "Zahnpasta" and gerichte[0]["extra_article"] == ""

    ergebnisse = list(csv.DictReader((ziel / "results.csv").open(encoding="utf-8")))
    assert len(ergebnisse) == 4
    assert set(ergebnisse[0]) >= {"run", "model", "dish", "path", "terms", "catalog_hits",
                                  "free_text", "hit_rate", "rejected", "seconds", "error", "phoenix_project"}
    zeile = next(e for e in ergebnisse if e["run"] == "b" and e["dish"] == "Pho Bo")
    assert zeile["model"] == "Nemotron" and zeile["terms"] == "18" and zeile["catalog_hits"] == "12"
    assert zeile["hit_rate"] == "0.667"
    assert zeile["phoenix_project"] == "Zettel Eval Nemotron"


def test_ohne_begriffe_bleibt_die_quote_leer_statt_null(hf, tmp_path):
    """Ein Zug ohne Stufe 1 (Rezeptweg, Fehler) hat keine Quote — `0.0` wäre
    eine Aussage, die niemand gemessen hat."""
    a = _lauf(tmp_path, "a", "Qwen", [_gericht("Ratatouille", "bereits gespeichert", 0, 0, weg="recipe")])
    hf.schreiben([a], tmp_path / "hf")
    zeile = list(csv.DictReader((tmp_path / "hf" / "results.csv").open(encoding="utf-8")))[0]
    assert zeile["hit_rate"] == "" and zeile["path"] == "recipe"
