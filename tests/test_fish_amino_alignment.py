import unittest

from nutrition_core import (
    FOOD_DB_VERSION,
    calculate,
    catalog_data,
    make_request,
    weight_applications,
)
from nutrition_core.engine import _DATA, canonical_json, digest
from nutrition_core.snapshots import loads_snapshot


EXPECTED = {
    '연어(MEXT10144)': {'류신': 1500, '이소류신': 900, '발린': 1100, '페닐알라닌': 810, '티로신': 710, '트레오닌': 1000, '메티오닌': 590, '리신': 1800, '트립토판': 230, '히스티딘': 540, '아르기닌': 1300},
    '고등어(MEXT10154)': {'류신': 1600, '이소류신': 960, '발린': 1100, '페닐알라닌': 840, '티로신': 710, '트레오닌': 1000, '메티오닌': 690, '리신': 1800, '트립토판': 230, '히스티딘': 1300, '아르기닌': 1200},
    '정어리(생)': {'류신': 1500, '이소류신': 910, '발린': 1000, '페닐알라닌': 800, '티로신': 670, '트레오닌': 950, '메티오닌': 570, '리신': 1800, '트립토판': 220, '히스티딘': 1000, '아르기닌': 1100},
}

FOODS = {
    '연어(MEXT10144)': ('연어 (Salmon)', 3500, 1040),
    '고등어(MEXT10154)': ('고등어 (Mackerel)', 3660, 1070),
    '정어리(생)': ('정어리 (Sardine, 생/생식용)', 3410, 1020),
}


class FishAminoAlignmentTests(unittest.TestCase):
    def test_exact_numeric_rows(self):
        data = catalog_data()
        for key, expected in EXPECTED.items():
            with self.subTest(key=key):
                self.assertEqual(data['amino_db'][key], expected)

    def test_exact_food_mappings(self):
        mapping = catalog_data()['amino_name_map']
        self.assertEqual(mapping['연어 (Salmon)'], '연어(MEXT10144)')
        self.assertEqual(mapping['고등어 (Mackerel)'], '고등어(MEXT10154)')
        self.assertEqual(mapping['정어리 (Sardine, 생/생식용)'], '정어리(생)')
        self.assertIsNone(mapping['정어리 (Sardine, 뼈제외/가식부/화식용)'])
        self.assertIsNone(mapping['정어리 (Sardine, 전체/뼈포함/무염통조림/화식용)'])

    def test_source_metadata_is_separate_and_exact(self):
        data = catalog_data()
        metadata = data['AMINO_SOURCE_METADATA']
        self.assertEqual(set(metadata), set(EXPECTED))
        self.assertEqual(metadata['연어(MEXT10144)']['source_id'], '10144')
        self.assertEqual(metadata['고등어(MEXT10154)']['source_id'], '10154')
        self.assertEqual(metadata['정어리(생)']['source_id'], '10047')
        for key in EXPECTED:
            self.assertEqual(metadata[key]['basis'], 'edible portion per 100 g')
            self.assertEqual(metadata[key]['unit'], 'mg/100 g')
            self.assertEqual(metadata[key]['normalization'], 'identity')
            self.assertTrue(metadata[key]['source_url'].startswith('https://fooddb.mext.go.jp/'))

    def test_100g_golden_results(self):
        for key, (food, expected_bcaa, expected_phe_trp) in FOODS.items():
            with self.subTest(food=food):
                result = calculate(make_request({food: 100}), 'review')
                self.assertEqual(result['amino'], EXPECTED[key])
                self.assertEqual(result['bcaa'], expected_bcaa)
                self.assertEqual(result['phenylalanine_plus_tryptophan'], expected_phe_trp)
                self.assertNotIn(food, result['coverage']['amino_missing'])

    def test_nonraw_sardines_are_unregistered_for_amino_coverage(self):
        for food in ('정어리 (Sardine, 뼈제외/가식부/화식용)', '정어리 (Sardine, 전체/뼈포함/무염통조림/화식용)'):
            with self.subTest(food=food):
                result = calculate(make_request({food: 100}, cooked=True, method='삶기'), 'review')
                self.assertIn(food, result['coverage']['amino_missing'])
                self.assertEqual(result['bcaa'], 0)

    def test_raw_input_and_cooked_input_weight_bases_are_unchanged(self):
        food = '닭가슴살 (Chicken Breast)'
        raw_input = make_request({food: 50}, cooked=True, method='삶기', weight_basis_mode='raw_input')
        cooked_input = make_request({food: 50}, cooked=True, method='삶기', weight_basis_mode='cooked_input')
        raw_weights = weight_applications(raw_input)[0]
        cooked_weights = weight_applications(cooked_input)[0]
        yield_ratio = _DATA['COOKING_YIELD']['삶기']['meat']
        self.assertEqual(raw_weights['nutrition_base_grams'], 50)
        self.assertEqual(raw_weights['applied_grams'], round(50 * yield_ratio))
        self.assertEqual(cooked_weights['applied_grams'], 50)
        self.assertAlmostEqual(cooked_weights['nutrition_base_grams'], 50 / yield_ratio)
        self.assertGreater(calculate(cooked_input, 'review')['kcal'], calculate(raw_input, 'review')['kcal'])

    def test_fruit_puree_and_precooked_weight_exceptions_are_unchanged(self):
        cases = (
            ('블루베리 (Blueberry)', '생과일 실제 급여량'),
            ('당근 퓨레 (Carrot)', '완성 퓨레'),
            ('익힌 홍합 (Green-Lipped Mussel)', '기등록 익힌 재료 실제 급여량'),
        )
        for food, expected_basis in cases:
            with self.subTest(food=food):
                row = weight_applications(make_request({food: 50}, cooked=True, method='삶기'))[0]
                self.assertEqual(row['input_grams'], 50)
                self.assertEqual(row['applied_grams'], 50)
                self.assertEqual(row['nutrition_base_grams'], 50)
                self.assertEqual(row['input_basis'], expected_basis)

    def test_food_db_version_hash_includes_amino_metadata(self):
        keys = ['db_data', 'omega_db', 'amino_db', 'amino_name_map', 'FRUIT_RAW_ITEMS', 'PREPARED_PUREE_ITEMS', 'FISH_SOURCE_METADATA', 'AMINO_SOURCE_METADATA']
        expected = 'food-' + digest({key: _DATA[key] for key in keys})[:16]
        self.assertEqual(FOOD_DB_VERSION, expected)

    def test_legacy_snapshot_payload_remains_loadable(self):
        request = make_request({'닭가슴살 (Chicken Breast)': 50})
        result = calculate(request, 'review')
        result.update({
            'engine_version': '1.4.0-fish-source-aligned',
            'food_db_version': 'food-legacy-reference',
            'calculation_policy_version': 'policy-legacy-reference',
        })
        body = {
            'schema_version': 'diet-snapshot-v1',
            'original_input': {'legacy': True},
            'request': request,
            'result': result,
            'policy_snapshot': {'version': 'legacy'},
        }
        snapshot = {**body, 'snapshot_hash': digest(body)}
        loaded = loads_snapshot(canonical_json(snapshot))
        self.assertEqual(loaded['result']['food_db_version'], 'food-legacy-reference')
        self.assertEqual(loaded['result']['input_hash'], digest(request))


if __name__ == '__main__':
    unittest.main()
