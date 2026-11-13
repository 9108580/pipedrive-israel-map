import unittest
from unittest.mock import patch
from src.localities import resolve_locality, lookup, registry
from src.state_store import state_to_geojson
from src.pipedrive_client import PipedriveClient

class LocalityTests(unittest.TestCase):
    def test_city_and_street_share_identity(self):
        for raw in ['נהריה', 'נהריה נחליאלי 3', 'נחליאלי 3, נהריה', 'שי עגנון 5, נהריה', 'Nahariya']:
            with self.subTest(raw=raw):
                self.assertEqual(resolve_locality(raw=raw)['id'], '9100')

    def test_raw_city_survives_lossy_google_format(self):
        cases = [("בית ג'ן", 'Beit-Gan, Yavneel, Israel', 'Yavneel', "בית ג'ן"),
                 ('יאנוח', 'Yarka, Israel', 'Yarka', "יאנוח-ג'ת"),
                 ('צפרירים 43', 'Tsafririm St 43, Haifa, Israel', 'Haifa', 'צפרירים'),
                 ('יוסף בורג 11 קריית טבעון', 'Jerusalem, Israel', 'Jerusalem', 'קרית טבעון')]
        for raw, formatted, locality, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(resolve_locality(raw=raw, formatted=formatted, locality=locality)['id'],lookup(expected))

    def test_shared_street_does_not_merge_cities(self):
        self.assertNotEqual(resolve_locality(raw='הרצל 5, חיפה')['id'], resolve_locality(raw='הרצל 5, נתניה')['id'])

    def test_distinct_settlements_stay_distinct(self):
        for a,b in [("בית ג'ן",'יבנאל'),('יקנעם עילית','יקנעם (מושבה)'),('נוה צוף','ערד'),('תרדיון','מרכז אזורי משגב')]:
            with self.subTest(a=a):self.assertNotEqual(lookup(a),lookup(b))

    def test_street_alone_never_becomes_new_locality(self):
        self.assertIsNone(resolve_locality(raw='אקסודוס 5')['id'])
        self.assertIsNone(resolve_locality(raw='')['id'])

    def test_review_is_guarded_by_exact_address(self):
        self.assertEqual(resolve_locality(raw='הדס 20',person_id=1870)['id'],'1139')
        self.assertEqual(resolve_locality(raw='נהריה',person_id=1870)['id'],'9100')
        self.assertIsNone(resolve_locality(raw='הדס 20',person_id=999999)['id'])

    def test_ambiguous_address_is_explicit(self):
        self.assertIsNone(resolve_locality(raw='טללים 6',formatted='Tlalim St 6, Tzur Hadassah, Israel',locality='Tzur Hadassah',person_id=2558)['id'])

    def test_unknown_pin_preserved_without_false_label(self):
        records={str(i):{'deal_id':i,'project_number':i,'lat':32+i/100,'lon':35,'city_key':'Street','address_type':'city','locality_id':lid} for i,lid in [(1,'9100'),(2,None)]}
        fs=state_to_geojson({'deals':records})['features']
        self.assertEqual(len(fs),2)
        self.assertEqual(fs[0]['properties']['place'],'נהריה')
        self.assertEqual(fs[1]['properties']['place'],'')
        self.assertEqual(fs[1]['geometry']['coordinates'],[35,32.02])

    def test_field_discovery_uses_main_field(self):
        c=PipedriveClient(token='test')
        c._get=lambda *a,**k:{'data':[{'key':'x_formatted_address','field_type':'varchar'},{'key':'x_locality','field_type':'varchar'},{'key':'x','field_type':'address'}]}
        self.assertEqual(c.discover_address_fields(),['x','postal_address'])
        r=c.get_person_location({'id':999,'x':'נהריה נחליאלי 3','x_formatted_address':'Nahlieli 3','x_locality':'Nahariya'},['x'])
        self.assertEqual(r['locality_id'],'9100')
        self.assertEqual(r['address_raw'],'נהריה נחליאלי 3')

    def test_unchanged_address_refreshes_identity(self):
        from src.sync import sync
        rec={'deal_id':1,'person_id':1,'project_number':1,'address':'same','lat':32,'lon':35,'snapped_to_building':True}
        st={'deals':{'1':rec},'next_project_number':2}
        with patch('src.sync.config.require_token'),patch('src.sync.PipedriveClient') as C,patch('src.sync.load_state',return_value=st),patch('src.sync.save_state'),patch('src.sync.write_geojson'),patch('src.sync.write_unmapped_report'), patch('src.sync.config.UNMAPPED_PATH') as report:
            report.read_text.return_value='{"generated_at":"test","crm_deals_total":1,"unmapped_total":0}'
            C.return_value.iter_deals_with_address.return_value=iter([{'deal_id':1,'person_id':1,'address':'same','locality_id':'9100'}])
            self.assertEqual(sync(),0)
        self.assertEqual(rec['locality_id'],'9100')
        self.assertEqual((rec['lat'],rec['lon']),(32,35))

if __name__=='__main__':unittest.main()
