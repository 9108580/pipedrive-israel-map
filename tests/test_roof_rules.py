"""Roof placement must never turn a settlement centroid into a map pin."""
import unittest
from unittest.mock import patch

from src.geocode import haversine_m
from src.roof_catalog import pick_roof, resolve_place_key
from src.scatter import NoSuitableBuilding, ResidentialScatter, _inside


def square(lat: float, lon: float, metres: float = 12) -> list[dict[str, float]]:
    dlat = metres / 111_320
    dlon = metres / 93_000
    return [{"lat": a, "lon": b} for a, b in (
        (lat - dlat, lon - dlon), (lat - dlat, lon + dlon),
        (lat + dlat, lon + dlon), (lat + dlat, lon - dlon),
        (lat - dlat, lon - dlon),
    )]


class RoofRulesTest(unittest.TestCase):
    def test_sport_and_school_grounds_are_excluded(self) -> None:
        center = (33.017, 35.347)
        ordinary = (33.0162, 35.3462)
        sports = (33.0174, 35.3474)
        school = (33.0176, 35.3476)
        elements = [
            {"id": 1, "tags": {"building": "yes"}, "geometry": square(*ordinary)},
            {"id": 2, "tags": {"building": "yes"}, "geometry": square(*sports)},
            {"id": 3, "tags": {"building": "yes"}, "geometry": square(*school)},
            {"id": 4, "tags": {"leisure": "pitch"}, "geometry": square(*sports, 35)},
            {"id": 5, "tags": {"amenity": "school"}, "geometry": square(*school, 35)},
        ]
        scatter = ResidentialScatter()
        scatter._query_overpass = lambda _query: elements
        roofs = scatter.fetch_buildings(*center)
        self.assertEqual(len(roofs), 1)
        self.assertLess(haversine_m(*roofs[0], *ordinary), 2)
        self.assertTrue(_inside(*roofs[0], [(p["lat"], p["lon"]) for p in elements[0]["geometry"]]))

    def test_no_city_marker_without_a_cluster(self) -> None:
        scatter = ResidentialScatter()
        with self.assertRaises(NoSuitableBuilding):
            scatter.pick_point(33.017, 35.347, [], seed=1, candidates=[(33.017, 35.347)])

    def test_same_roof_never_gets_an_offset(self) -> None:
        catalog = {"places": {"village": {"roofs": [[33.017, 35.347]]}}}
        with patch("src.roof_catalog.load_catalog", return_value=catalog):
            first = pick_roof("village", [], seed=1)
            second = pick_roof("village", [first], seed=2)
        self.assertEqual(first, second)

    def test_street_pin_requires_nearby_roof(self) -> None:
        scatter = ResidentialScatter()
        with self.assertRaises(NoSuitableBuilding):
            scatter.snap_to_building(33.017, 35.347, [], seed=1, candidates=[(33.02, 35.35)])

    def test_explicit_crm_locality_beats_wrong_geocoder_street(self) -> None:
        places = {"עדי": {}, "ברקת": {}, "אלפי מנשה": {}, "שניר": {}}
        self.assertEqual(resolve_place_key("Bareket St, Adi, Israel", "ברקת", "ברקת, עדי", places), "עדי")
        self.assertEqual(resolve_place_key("Snir St 12, Alfei Menashe", "שניר", "שניר, גליל", places), "אלפי מנשה")

    def test_bare_ambiguous_street_does_not_override_geocoder(self) -> None:
        places = {"משואה": {}, "ירושלים": {}}
        self.assertEqual(resolve_place_key("משואה 314", "ירושלים", "גבעת משואה, ירושלים", places), "ירושלים")

    def test_sports_ground_geocode_yields_to_crm_village(self) -> None:
        places = {"ראש הנקרה": {}, "מגרש ספורט כפר ראש הנקרה": {}}
        self.assertEqual(
            resolve_place_key("נוף הים, Kfar Rosh HaNikra, Israel",
                              "מגרש ספורט כפר ראש הנקרה", "מגרש ספורט כפר ראש הנקרה, ראש הנקרה", places),
            "ראש הנקרה",
        )


if __name__ == "__main__":
    unittest.main()
