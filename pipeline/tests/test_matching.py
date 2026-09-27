"""Offline checks for pipeline/matching.py (no network). Run: python -m pipeline.tests.test_matching"""

from pipeline.matching import Wine, band, fold, match, queries, rank, score


def hit(id, name, winery, country, type_id, avg=4.0, n=1000):
    return {"id": id, "name": name, "type_id": type_id, "winery": {"name": winery},
            "region": {"country": country}, "statistics": {"ratings_average": avg, "ratings_count": n}}


def wine(bold, thin, producer, country="Frankrike", category="Rött vin", grapes=()):
    return Wine("1", "1", bold, thin or "", producer, "2022", country, category, list(grapes))


def best_band(w, hits):
    ranked = rank([score(w, h, "q") for h in hits])
    return ranked[0], band(ranked[0], ranked[1] if len(ranked) > 1 else None)


def test_fold():
    assert fold("Château d’Yquem") == "chateau d yquem"
    assert fold("Côtes-du-Rhône Villages") == "cotes du rhone villages"
    assert fold("Moët & Chandon") == "moet and chandon"


def test_exact_accept():
    w = wine("Crudo", "Nerello Mascalese Frappato", "Cantina Birgi", "Italien")
    c, b = best_band(w, [hit(1, "Crudo Nerello Mascalese - Frappato", "Cantina Birgi", "it", 1)])
    assert b == "accept", (c, b)


def test_other_winery_same_grapes_rejected():
    # The real top hit for this query: same grapes and region, different winery.
    w = wine("Crudo", "Nerello Mascalese Frappato", "Cantina Birgi", "Italien")
    c, b = best_band(w, [hit(99, "Frappato - Nerello Mascalese", "La Cantina Sotto l'Albero", "it", 1)])
    assert b != "accept", (c, b)


def test_producer_is_company_brand_in_name():
    w = wine("Castillo Monjardin", "Garnacha Organic", "Castillo de Monjardin", "Spanien")
    c, b = best_band(w, [hit(2, "Garnacha", "Castillo de Monjardín", "es", 1)])
    assert b == "accept", (c, b)
    w = wine("Château Joumes Fillon", "Sauvignon Blanc", "SCEA Raymond", "Frankrike", "Vitt vin")
    c, b = best_band(w, [hit(3, "Sauvignon Blanc", "Château Joumes-Fillon", "fr", 2)])
    assert b == "accept", (c, b)


def test_country_and_colour_contradictions():
    w = wine("Bordo", "Cabernet Sauvignon Reserva", "Finca Flichman", "Argentina")
    c, b = best_band(w, [hit(4, "Bordo Cabernet Sauvignon Reserva", "Finca Flichman", "cl", 1)])
    assert b == "reject" and c.contradictions
    c, b = best_band(w, [hit(5, "Bordo Cabernet Sauvignon Reserva", "Finca Flichman", "ar", 2)])
    assert b == "reject" and c.contradictions


def test_style_words_never_accept_alone():
    w = wine("Côté Mas", "Brut", "Domaines Paul Mas", "Frankrike", "Mousserande vin")
    c, b = best_band(w, [hit(6, "Brut", "Paul Mas", "fr", 3)])
    assert b != "accept", (c, b)


def test_rose_marker_and_reserva_matter():
    w = wine("Freixenet", "Cordon Negro Brut", "Freixenet", "Spanien", "Mousserande vin")
    right = hit(7, "Cordon Negro Brut", "Freixenet", "es", 3)
    rose = hit(8, "Cordon Negro Brut Rosé", "Freixenet", "es", 3)
    ranked = rank([score(w, h, "q") for h in (rose, right)])
    assert ranked[0].vivino_id == 7, ranked
    w = wine("Faustino", "I Gran Reserva", "Bodegas Faustino", "Spanien")
    ranked = rank([score(w, h, "q") for h in (hit(9, "VII Tinto", "Faustino", "es", 1),
                                                 hit(10, "I Gran Reserva", "Faustino", "es", 1))])
    assert ranked[0].vivino_id == 10, ranked


def test_generic_winery_word_is_not_producer_evidence():
    w = wine("Crudo", "Nerello Mascalese Frappato", "Cantina Birgi", "Italien")
    c = score(w, hit(99, "Frappato - Nerello Mascalese", "La Cantina Sotto l'Albero", "it", 1), "q")
    assert c.producer_score == 0 and band(c) == "reject", c


def test_one_sided_reserva_not_accepted():
    w = wine("Casillero del Diablo", "Cabernet Sauvignon", "Concha y Toro", "Chile")
    c = score(w, hit(14, "Casillero del Diablo Reserva Especial Cabernet Sauvignon", "Concha y Toro", "cl", 1), "q")
    assert band(c) == "review", c
    c = score(w, hit(13, "Casillero del Diablo Cabernet Sauvignon", "Viña Concha y Toro", "cl", 1), "q")
    assert band(c) == "accept", c


def test_near_tie_goes_to_review():
    w = wine("Chablis", None, "William Fèvre", "Frankrike", "Vitt vin")
    c, b = best_band(w, [hit(11, "Chablis", "William Fèvre", "fr", 2), hit(12, "Chablis", "Domaine William Fèvre", "fr", 2)])
    assert b == "review", (c, b)


def test_queries_fallbacks():
    w = wine("d'Arenberg", "Hermit Crab", "d'Arenberg", "Australien", "Vitt vin", ["Viognier"])
    qs = queries(w)
    assert qs[0] == "d arenberg hermit crab", qs  # producer already in the name: not repeated
    assert "d arenberg viognier" in qs
    w = wine("Crudo", "Nerello Mascalese Frappato", "Cantina Birgi", "Italien")
    assert queries(w)[0] == "birgi crudo nerello mascalese frappato"
    # corporate noise and descriptive filler never reach Algolia
    w = wine("Borgogno", "Barolo Riserva", "Giacomo Borgogno & Figli", "Italien", grapes=["Nebbiolo"])
    assert queries(w) == ["borgogno barolo riserva", "giacomo borgogno nebbiolo"], queries(w)
    w = wine("Doppio Passo", "Nero d'Avola Organic", "Botter Spa", "Italien")
    assert queries(w)[0] == "botter doppio passo nero d avola", queries(w)


def test_match_pools_all_queries():
    calls = []
    w = wine("Crudo", "Nerello Mascalese Frappato", "Cantina Birgi", "Italien")

    def search(q):
        calls.append(q)
        return [hit(1, "Crudo Nerello Mascalese - Frappato", "Cantina Birgi", "it", 1)]

    r = match(w, search)
    assert r["band"] == "accept" and len(calls) == len(queries(w)), calls
    calls.clear()
    assert match(w, search, stop_on_accept=True)["band"] == "accept" and len(calls) == 1


def test_later_query_competitor_makes_it_a_review():
    # The first query finds one good candidate; the second finds a near-identical one. Pooled, it's a tie.
    w = wine("Chablis", None, "William Fèvre", "Frankrike", "Vitt vin")
    results = iter([[hit(11, "Chablis", "William Fèvre", "fr", 2)], [hit(12, "Chablis", "Domaine William Fèvre", "fr", 2)]])
    r = match(w, lambda q: next(results, []))
    assert r["band"] == "review", r


def test_numbers_are_identity():
    w = wine("Lindeman's Bin 65", "Chardonnay", "Lindemans", "Australien", "Vitt vin")
    c = score(w, hit(20, "Bin 50 Chardonnay", "Lindeman's", "au", 2), "q")
    assert band(c) == "reject" and "number 65≠50" in c.contradictions, c
    assert band(score(w, hit(21, "Bin 65 Chardonnay", "Lindeman's", "au", 2), "q")) == "accept"
    # Age statements are numbers too; a number on one side only needs a human.
    w = wine("Graham's", "Tawny Port 10 Years", "W & J Graham's", "Portugal", "Starkvin")
    assert band(score(w, hit(22, "20 Year Old Tawny Port", "Graham's", "pt", 24), "q")) == "reject"
    w = wine("Cuvée No 7", "Brut", "Almadén", "Brasilien", "Mousserande vin")
    c = score(w, hit(23, "Cuvée Brut", "Almadén", "br", 3), "q")
    assert band(c) != "accept" and any("number" in u for u in c.unconfirmed), c


def test_frizzante_is_not_spumante():
    # Real false accept from the spike: same producer, semi-sparkling vs fully sparkling.
    w = wine("Pizzolato", "Moscato Frizzante Organic", "La Cantina Pizzolato", "Italien", "Mousserande vin")
    c = score(w, hit(24, "Moscato Spumante Dolce", "Pizzolato", "it", 3), "q")
    assert band(c) == "reject" and any("style" in x for x in c.contradictions), c
    # "Spumante" on one side only just restates the type.
    w = wine("Ecologica", "Moscato Spumante", "La Riojana", "Argentina", "Mousserande vin")
    c = score(w, hit(25, "Moscato", "Ecologica", "ar", 3), "q")
    assert not any("style" in u for u in c.unconfirmed), c


def test_sweetness_levels():
    w = wine("Valdo", "Prosecco Extra Dry", "Valdo", "Italien", "Mousserande vin")
    assert band(score(w, hit(26, "Prosecco Brut", "Valdo", "it", 3), "q")) == "reject"
    # Brut left out of the SB name is normal; a sweet level on one side only is not.
    w = wine("Moët & Chandon", "Rosé Impérial", "Moët & Chandon", "Frankrike", "Mousserande vin")
    assert band(score(w, hit(27, "Impérial Rosé Brut Champagne", "Moët & Chandon", "fr", 3), "q")) == "accept"
    w = wine("Ecologica", "Moscato Spumante", "La Riojana", "Argentina", "Mousserande vin")
    c = score(w, hit(28, "Moscato Spumante Dolce", "Ecologica", "ar", 3), "q")
    assert band(c) == "review" and "sweetness sweet" in c.unconfirmed, c


def test_tier_word_on_one_side_needs_review():
    w = wine("Black Stallion", "Napa Valley Cabernet Sauvignon", "Black Stallion Estate Winery", "USA")
    c = score(w, hit(29, "Premiere Napa Valley Cabernet Sauvignon", "Black Stallion", "us", 1), "q")
    assert band(c) == "review" and "tier premiere" in c.unconfirmed, c
    # A tier word that is part of the brand/winery doesn't count.
    w = wine("Grand Sud", "Merlot", "Les Grands Chais de France", "Frankrike")
    assert band(score(w, hit(30, "Merlot", "Grand Sud", "fr", 1), "q")) == "accept"


def test_one_sided_name_words_need_review():
    # Real spike cases: a different line from the same producer, with a long shared name.
    w = wine("Casillero del Diablo", "Cabernet Sauvignon", "Concha y Toro", "Chile", grapes=["Cabernet Sauvignon"])
    c = score(w, hit(33, "Leyenda Cabernet Sauvignon", "Casillero del Diablo", "cl", 1), "q")
    assert band(c) == "review" and "name words leyenda" in c.unconfirmed, c
    w = wine("This is The Idiot", "Chardonnay", "Enjoy Wine & Spirits", "Italien", "Vitt vin")
    c = score(w, hit(34, "This Is The Idiot Creamy Oak Chardonnay", "Enjoy Wine & Spirits", "it", 2), "q")
    assert band(c) == "review", c
    # Region, grape and wine-type words, and Swedish filler, may appear on one side only.
    w = wine("Juan Gil", "Silver Label", "Juan Gil", "Spanien")
    h = hit(35, "Jumilla Silver Label", "Juan Gil", "es", 1)
    h["region"]["name"] = "Jumilla"
    assert band(score(w, h, "q")) == "accept"
    w = wine("Casas Patronales", "Rich Cabernet sauvignon, carmenère och syrah.", "Casas Patronales", "Chile",
             grapes=["Cabernet Sauvignon", "Carmenère", "Syrah"])
    assert band(score(w, hit(36, "Rich Cabernet Sauvignon - Carmenere - Syrah", "Casas Patronales", "cl", 1), "q")) == "accept"
    w = wine("Graham's", "Late Bottled Vintage", "W & J Graham's", "Portugal", "Starkvin")
    assert band(score(w, hit(37, "Late Bottled Vintage Port", "Graham's", "pt", 24), "q")) == "accept"


def test_organic_versions_are_not_interchangeable():
    # Real audit error: the regular Les Fumées Blanches matched Vivino's separate organic entry.
    w = wine("Les Fumées Blanches", "Sauvignon Blanc", "François Lurton", "Frankrike", "Vitt vin", ["Sauvignon Blanc"])
    w.organic = False
    organic = hit(40, "Les Fumées Blanches Organic Sauvignon Blanc", "François Lurton", "fr", 2)
    regular = hit(41, "Les Fumées Blanches Sauvignon Blanc", "François Lurton", "fr", 2)
    c = score(w, organic, "q")
    assert band(c) == "review" and "organic on Vivino only" in c.unconfirmed, c
    assert rank([score(w, h, "q") for h in (organic, regular)])[0].vivino_id == 41
    # An organic wine: Vivino often omits the word, so a plain entry is still fine; an organic entry wins a tie.
    w.organic = True
    assert band(score(w, regular, "q")) == "accept"
    assert rank([score(w, h, "q") for h in (regular, organic)])[0].vivino_id == 40
    # "Ecologica" as a winery name is not an organic marker.
    w = wine("Ecologica", "Berlina Organic", "La Riojana", "Argentina")
    w.organic = True
    assert band(score(w, hit(42, "Berlina", "Ecologica", "ar", 1), "q")) == "accept"


def test_estate_is_part_of_a_wine_name():
    # Real audit error: "Estate Pinot Noir" matched the estate's single-vineyard "La Côte Pinot Noir".
    w = wine("Domaine de la Côte Estate", "Pinot Noir", "Domaine de la Côte", "USA", grapes=["Pinot noir"])
    c = score(w, hit(50, "La Côte Pinot Noir", "Domaine de la Côte", "us", 1), "q")
    assert band(c) != "accept" and "name words estate" in c.unconfirmed, c
    # ...but "Estate" in the producer's own name is still just the producer.
    w = wine("Black Mountain", "Shiraz Cabernet", "Kingston Estate Wines", "Australien")
    assert band(score(w, hit(51, "Black Mountain Shiraz - Cabernet", "Kingston", "au", 1), "q")) == "accept"


def test_missing_evidence_is_not_agreement():
    w = wine("Crudo", "Nerello Mascalese Frappato", "Cantina Birgi", "Italien")
    c = score(w, hit(31, "Crudo Nerello Mascalese - Frappato", "Cantina Birgi", "", 1), "q")
    assert band(c) == "review" and "country unknown" in c.unconfirmed, c
    w = wine("Martini", "Rosso", "Martini & Rossi", "Italien", "Vermouth")
    c = score(w, hit(32, "Rosso", "Martini", "it", 1), "q")
    assert band(c) != "accept" and "type not checked" in c.unconfirmed, c


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            n += 1
            print("ok", name)
    print(f"{n} tests passed")
