"""Boundary identity regressions: child transfers are not whole-agency succession."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from scripts.agency_registry.build_official_index import (
    build_derived, filter_boundary_identity, institution_map,
)


class BoundaryIdentityTests(unittest.TestCase):
    def rows(self, alive_parent=False):
        def row(code, *, alive=False, parent=None, prev=None):
            return dict(code=code, name=f"{parent} {code}" if parent else code,
                        alive=alive, parent=parent, prev=prev, created="20200101",
                        closed=None if alive else "20260101", type_top="01",
                        type_mid="02" if parent else "08", level="2" if parent else "1")
        return {r['code']: r for r in [row('1000000', alive=alive_parent),
            row('1000001', parent='1000000'), row('1000002', parent='1000000'),
            row('2000000', alive=True, prev='1000001'),
            row('3000000', alive=True, prev='1000002')]}

    def test_split_removed_and_forward_cannot_rejoin(self):
        rows = self.rows()
        d = build_derived(rows)
        self.assertEqual(d['stats']['boundary_split_dropped'], 2)
        self.assertEqual(d['link_pairs'], {})
        self.assertEqual(d['forward'], {'1000001': '2000000', '1000002': '3000000'})
        inst = institution_map(d['link_pairs'], [], d, rows)
        self.assertEqual(inst['2000000'], 'ag-c2000000')
        self.assertEqual(inst['3000000'], 'ag-c3000000')
        self.assertEqual(d['alive_boundaries'], ['2000000', '3000000'])
        reverse = build_derived(dict(reversed(list(rows.items()))))
        self.assertEqual(institution_map(reverse['link_pairs'], [], reverse, rows), inst)

    def test_live_parent_child_departure_is_not_identity(self):
        rows = self.rows(alive_parent=True)
        del rows['1000002'], rows['3000000']
        d = build_derived(rows)
        links, _, stats = filter_boundary_identity(d['link_pairs'], [], d['forward'], rows)
        self.assertEqual(links, {})
        self.assertEqual(stats['boundary_identity_split_sources'], 1)
        inst = institution_map(d['link_pairs'], [], d, rows)
        self.assertNotEqual(inst['1000000'], inst['2000000'])

    def test_single_chain_and_seed_override(self):
        rows = self.rows()
        d = build_derived(rows)
        seed = [dict(from_code='1000000', to_code='2000000', institution_id='ag-reviewed')]
        conflicting = {('1000000', '3000000'): {'from_code': '1000000', 'to_code': '3000000'}}
        inst = institution_map(conflicting, seed, d, rows)
        self.assertEqual(inst['1000000'], 'ag-reviewed')
        self.assertEqual(inst['2000000'], 'ag-reviewed')
        self.assertEqual(inst['3000000'], 'ag-c3000000')
        del rows['1000002'], rows['3000000']
        d = build_derived(rows)
        inst = institution_map(d['link_pairs'], [], d, rows)
        self.assertEqual(inst['1000000'], inst['2000000'])

    def test_indirect_split_filters_original_graph_before_union(self):
        rows = self.rows()
        rows['4000000'] = dict(rows['1000000'], code='4000000', name='4000000')
        links = {('1000000', '4000000'): {}, ('4000000', '3000000'): {}}
        forward = {'1000001': '2000000'}
        kept, identity_forward, stats = filter_boundary_identity(links, [], forward, rows)
        self.assertNotIn(('1000000', '4000000'), kept)
        self.assertEqual(identity_forward, {})
        self.assertEqual(stats['boundary_identity_split_sources'], 1)
        from scripts.agency_registry.build_official_index import institution_components
        inst = institution_components(links, [], forward, rows)
        self.assertNotIn('1000000', inst)
        self.assertEqual(inst['4000000'], inst['3000000'])

    def test_invalid_seed_cannot_merge_two_alive_boundaries(self):
        rows = self.rows()
        d = build_derived(rows)
        seed = [dict(from_code='2000000', to_code='3000000', institution_id='ag-invalid')]
        with self.assertRaisesRegex(ValueError, 'alive boundaries share institution_id'):
            institution_map(d['link_pairs'], seed, d, rows)

    def test_validator_rejects_collision_even_with_matching_hashes(self):
        import hashlib
        import shutil
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            snap = Path(tmp) / 'registry'
            shutil.copytree(root / 'shared/agency-region-registry', snap)
            file = snap / 'data/agency_institutions.json'
            blob = json.loads(file.read_text())
            blob['institutions']['1282071'] = blob['institutions']['1282083']
            file.write_text(json.dumps(blob))
            manifest = json.loads((snap / 'manifest.json').read_text())
            manifest['files']['data/agency_institutions.json'] = hashlib.sha256(file.read_bytes()).hexdigest()
            (snap / 'manifest.json').write_text(json.dumps(manifest))
            proc = subprocess.run([sys.executable, str(root / 'scripts/agency_registry/validate.py'),
                                   '--snapshot', str(snap)], capture_output=True, text=True)
            self.assertEqual(proc.returncode, 1)
            self.assertIn('alive boundaries share institution_id', proc.stdout)
            self.assertNotIn('hash mismatch', proc.stdout)
