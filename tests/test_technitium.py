import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('technitium_config', Path(__file__).resolve().parents[1] / 'technitium/linux/configure.py')
dns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dns)


class FakeApi:
    def __init__(self, records):
        self.records, self.writes = records, []
        self.zones = [{'name': 'example.com', 'type': 'Primary', 'internal': False}]

    def call(self, path, **params):
        if path == '/api/zones/list':
            return {'response': {'zones': self.zones}}
        if path == '/api/zones/records/get':
            return {'response': {'records': self.records}}
        self.writes.append((path, params))
        return {'status': 'ok'}


class TechnitiumTests(unittest.TestCase):
    def read(self, records, **options):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'records.json'
            path.write_text(json.dumps({'zone': 'example.com', 'records': records}))
            return dns.read_records(path, **options)

    def test_panel_record_is_added_and_foreign_names_are_rejected(self):
        zone, records = self.read([], domain='dns.example.com', listen='192.168.0.250')
        self.assertEqual((zone, records[0]['name'], records[0]['value']), ('example.com', 'dns.example.com', '192.168.0.250'))
        with self.assertRaises(ValueError):
            self.read([dict(name='cdn.other.com.', type='A', value='192.168.0.200')])

    def test_relative_names_and_existing_fqdns_resolve_to_same_record(self):
        for relative in ('cdn', 'postgresql.server1', 'kubernetes.cluster1'):
            fqdn = relative + '.example.com'
            for name in (relative, fqdn, fqdn + '.'):
                with self.subTest(name=name):
                    _, records = self.read([dict(name=name, type='A', value='192.168.0.200')])
                    self.assertEqual(records[0]['name'], fqdn)
            with self.subTest(duplicate=relative), self.assertRaises(ValueError):
                self.read([dict(name=name, type='A', value='192.168.0.200') for name in (relative, fqdn)])

    def test_relative_name_cannot_exceed_dns_length_after_adding_zone(self):
        name = '.'.join(['a' * 63] * 3 + ['b' * 50])
        with self.assertRaises(ValueError):
            self.read([dict(name=name, type='A', value='192.168.0.200')])

    def test_open_recursion_and_self_forwarding_are_rejected(self):
        for listen, cidrs, upstream in [('192.168.0.250', '0.0.0.0/0', ''),
                                      ('192.168.0.250', '192.168.0.1/24', ''),
                                      ('192.168.0.250', '192.168.0.0/24', '192.168.0.250'),
                                      ('0.0.0.0', '192.168.0.0/24', '')]:
            with self.subTest(listen=listen, cidrs=cidrs, upstream=upstream), self.assertRaises(ValueError):
                dns.validate_network(listen, cidrs, upstream)
        dns.validate_network('192.168.0.250', '192.168.0.0/24', '1.1.1.1,9.9.9.9')

    def test_cname_and_ttl_conflicts_are_rejected(self):
        for records in [[dict(name='cdn', type='CNAME', value='target.example.com'), dict(name='cdn', type='A', value='192.168.0.200')],
                        [dict(name='cdn', type='A', value='192.168.0.200', ttl=300), dict(name='cdn', type='A', value='192.168.0.201', ttl=600)]]:
            with self.assertRaises(ValueError):
                self.read(records)

    def test_conflict_is_detected_before_any_record_write(self):
        api = FakeApi([dict(name='cdn.example.com', type='A', ttl=300, rData={'ipAddress': '192.168.0.200'})])
        records = [dict(name='new.example.com', type='A', value='192.168.0.202', ttl=300),
                   dict(name='cdn.example.com', type='A', value='192.168.0.201', ttl=300)]
        with self.assertRaises(ValueError):
            dns.apply_records(api, 'example.com', records)
        self.assertEqual(api.writes, [])

    def test_identical_records_are_preserved(self):
        api = FakeApi([dict(name='cdn.example.com', type='A', ttl=300, rData={'ipAddress': '192.168.0.200'})])
        dns.apply_records(api, 'example.com', [dict(name='cdn.example.com', type='A', value='192.168.0.200', ttl=300)])
        self.assertEqual(api.writes, [])

    def test_new_zone_restricts_queries_and_disables_transfers(self):
        api = FakeApi([])
        api.zones = []
        dns.apply_records(api, 'example.com', [], cidrs='192.168.0.0/24')
        options = next(params for path, params in api.writes if path == '/api/zones/options/set')
        self.assertEqual(options['queryAccess'], 'UseSpecifiedNetworkACL')
        self.assertEqual(options['queryAccessNetworkACL'], '192.168.0.0/24')
        self.assertEqual((options['zoneTransfer'], options['update']), ('Deny', 'Deny'))

    def test_replace_overwrites_only_first_value_of_selected_rrset(self):
        api = FakeApi([dict(name='cdn.example.com', type='A', ttl=300, rData={'ipAddress': '192.168.0.200'})])
        records = [dict(name='cdn.example.com', type='A', value='192.168.0.201', ttl=300),
                   dict(name='cdn.example.com', type='A', value='192.168.0.202', ttl=300)]
        dns.apply_records(api, 'example.com', records, replace=True)
        self.assertEqual([params['overwrite'] for _, params in api.writes], ['true', 'false'])
        self.assertTrue(all(path == '/api/zones/records/add' for path, _ in api.writes))


if __name__ == '__main__':
    unittest.main()
