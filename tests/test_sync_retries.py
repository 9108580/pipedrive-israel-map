"""Exercise durable retry and publication with real temporary state/GeoJSON."""
import copy
import hashlib
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, Mock

import requests

from src import config
from src.scatter import BuildingLookupError, NoSuitableBuilding, ResidentialScatter, OVERPASS_MIRRORS
from src.state_store import save_state, load_state, write_geojson
from src.sync import sync
from src.validate_map import validate


class SyncRetryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name, value in {
            "DATA_DIR": self.root / "data", "MAP_DATA_DIR": self.root / "map/data",
            "STATE_PATH": self.root / "data/state.json",
            "GEOJSON_PATH": self.root / "map/data/projects.geojson",
            "UNMAPPED_PATH": self.root / "data/unmapped_deals.json",
        }.items():
            self.stack.enter_context(patch.object(config, name, value))
        self.stack.enter_context(patch("src.validate_map.ROOT", self.root))
        self.stack.enter_context(patch("src.sync.config.require_token"))
        self.client = self.stack.enter_context(patch("src.sync.PipedriveClient")).return_value
        self.geo = self.stack.enter_context(patch("src.sync.NominatimGeocoder")).return_value
        self.geo.geocode.return_value = SimpleNamespace(
            lat=32, lon=35, city="Test Town", display_name="Test Town", is_city_level=True)
        self.scatter = self.stack.enter_context(patch("src.sync.ResidentialScatter")).return_value
        self.stack.enter_context(patch("src.sync.load_catalog", return_value={"places": {}}))
        self.stack.enter_context(patch("src.sync.resolve_place_key", return_value="test"))
        self.stack.enter_context(patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": str(self.root / "summary.md")}))
        self.old = {"deal_id": 1, "person_id": 1, "project_number": 1,
                    "address": "Old address", "lat": 32, "lon": 35,
                    "snapped_to_building": True, "locality_id": "9100"}
        save_state({"deals": {"1": self.old}, "next_project_number": 2})
        write_geojson(load_state())

    def rows(self, *rows):
        self.client.persons_address_index.return_value = {r["person_id"]: {} for r in rows}
        self.client.iter_deals_with_address.side_effect = lambda _: iter(copy.deepcopy(rows))
        self.client.iter_deals.side_effect = lambda: iter(
            [{"id": r["deal_id"], "person_id": r["person_id"]} for r in rows])
        self.client.deal_person_id.side_effect = lambda d: d["person_id"]

    def report(self):
        return json.loads(config.UNMAPPED_PATH.read_text(encoding="utf-8"))

    def test_outage_continues_persists_and_next_run_recovers_without_duplicates(self):
        self.rows({"deal_id": 2, "person_id": 2, "address": "Two"},
                  {"deal_id": 3, "person_id": 3, "address": "Three"})
        self.scatter.pick_point.side_effect = [BuildingLookupError("outage"), (32.1, 35.1)]
        self.assertEqual(sync(), 1)
        state = load_state()
        self.assertEqual(set(state["deals"]), {"1", "3"})
        self.assertEqual(state["deals"]["1"], self.old)
        self.assertEqual(state["pending_deals"]["2"]["attempts"], 1)
        self.assertEqual(self.report()["deferred_total"], 1)
        self.assertIn("building_service_unavailable", (self.root / "summary.md").read_text())
        self.assertEqual(validate()["visible"], 2)
        self.scatter.pick_point.side_effect = [(32.2, 35.2)]
        self.assertEqual(sync(), 1)
        state = load_state()
        self.assertEqual(set(state["deals"]), {"1", "2", "3"})
        self.assertEqual(state["pending_deals"], {})
        self.assertEqual(self.report()["deferred_total"], 0)
        self.assertEqual(validate()["visible"], 3)
        self.assertEqual(sync(), 0)
        self.assertEqual(validate()["visible"], 3)

    def test_changed_address_preserves_entire_old_record_until_retry_succeeds(self):
        self.rows({"deal_id": 1, "person_id": 1, "address": "New address",
                   "locality_id": "4000", "address_raw": "New address"})
        self.scatter.pick_point.side_effect = BuildingLookupError("outage")
        self.assertEqual(sync(), 0)
        self.assertEqual(load_state()["deals"]["1"], self.old)
        self.assertTrue(load_state()["pending_deals"]["1"]["previous_pin_preserved"])
        self.assertEqual(validate()["visible"], 1)
        self.assertEqual(sync(), 0)
        self.assertEqual(load_state()["pending_deals"]["1"]["attempts"], 2)
        self.scatter.pick_point.side_effect = None
        self.scatter.pick_point.return_value = (32.3, 35.3)
        self.assertEqual(sync(), 1)
        state = load_state()
        self.assertEqual(state["deals"]["1"]["project_number"], 1)
        self.assertEqual(state["deals"]["1"]["address"], "New address")
        self.assertEqual(state["pending_deals"], {})
        self.assertEqual(validate()["visible"], 1)

    def test_missing_geocode_is_deferred_without_blocking_later_deal(self):
        good = self.geo.geocode.return_value
        self.geo.geocode.side_effect = [None, good]
        self.rows({"deal_id": 2, "person_id": 2, "address": "Two"},
                  {"deal_id": 3, "person_id": 3, "address": "Three"})
        self.scatter.pick_point.return_value = (32.1, 35.1)
        self.assertEqual(sync(), 1)
        self.assertEqual(load_state()["pending_deals"]["2"]["reason"], "geocode_unavailable")
        self.assertEqual(validate()["visible"], 2)

    def test_no_roof_does_not_publish_unverified_pin(self):
        self.rows({"deal_id": 2, "person_id": 2, "address": "Two"})
        self.scatter.pick_point.side_effect = NoSuitableBuilding("empty")
        self.assertEqual(sync(), 0)
        self.assertEqual(load_state()["pending_deals"]["2"]["reason"], "no_verified_roof")
        self.assertEqual(validate()["visible"], 1)

    def test_street_lookup_outage_does_not_use_unrelated_catalog_fallback(self):
        self.rows({"deal_id": 2, "person_id": 2, "address": "Street 22"})
        self.geo.geocode.return_value.is_city_level = False
        self.stack.enter_context(patch("src.sync.is_city_only_address", return_value=False))
        nearest = self.stack.enter_context(patch("src.sync.pick_nearest_roof"))
        self.scatter.snap_to_building.side_effect = BuildingLookupError("outage")
        self.assertEqual(sync(), 0)
        nearest.assert_not_called()
        self.assertEqual(validate()["visible"], 1)

    def test_programming_errors_still_fail_run(self):
        self.rows({"deal_id": 2, "person_id": 2, "address": "Two"})
        self.scatter.pick_point.side_effect = ValueError("programming error")
        with self.assertRaisesRegex(ValueError, "programming error"):
            sync()
        self.assertEqual(validate()["visible"], 1)


class MirrorOutageTests(unittest.TestCase):
    @patch("src.scatter.time.sleep")
    @patch.object(ResidentialScatter, "_throttle")
    def test_mirrors_bounded_circuit_keeps_cache_and_resets_next_run(self, throttle, sleep):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scatter = ResidentialScatter(cache_dir=root)
            scatter.session = Mock()
            scatter.session.get.side_effect = requests.Timeout("offline")
            for query in ["first", "second"]:
                with self.assertRaises(BuildingLookupError):
                    scatter._query_overpass(query)
            self.assertEqual(scatter.session.get.call_count, len(OVERPASS_MIRRORS))
            cached = root / (hashlib.sha256(b"cached").hexdigest() + ".json")
            cached.write_text('[{"id": 42}]')
            self.assertEqual(scatter._query_overpass("cached"), [{"id": 42}])
            fresh = ResidentialScatter(cache_dir=root)
            fresh.session = Mock()
            fresh.session.get.return_value.status_code = 200
            fresh.session.get.return_value.json.return_value = {"elements": [{"id": 43}]}
            self.assertEqual(fresh._query_overpass("first"), [{"id": 43}])
            self.assertEqual(fresh.session.get.call_count, 1)


if __name__ == "__main__":
    unittest.main()
