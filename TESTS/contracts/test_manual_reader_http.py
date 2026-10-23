"""Portable manual reader HTTP contract; actual adapter observations are required."""
import json
import os
from pathlib import Path
import unittest
FIXTURE=Path(__file__).resolve().parents[1]/'fixtures/manual-reader-http.json'
def validate_report(report,fixture):
 if report.get('schema_version')!=1 or set(report.get('cases',{}))!=set(fixture['cases']):raise AssertionError('Manual HTTP case set differs; no skipped cases')
 for name,spec in fixture['cases'].items():
  actual=report['cases'][name]
  for key,expected in spec['expected'].items():
   if key=='headers':
    for header,value in expected.items():
     if actual.get('headers',{}).get(header)!=value:raise AssertionError(f'{name}: header {header} differs')
   elif actual.get(key)!=expected:raise AssertionError(f'{name}.{key}: expected {expected!r}, observed {actual.get(key)!r}')
class ManualReaderHTTPContracts(unittest.TestCase):
 def test_actual_observations(self):
  path=os.environ.get('TESTS_MANUAL_READER_HTTP_REPORT');self.assertTrue(path,'Real HTTP adapter report required')
  report=json.loads(Path(path).read_text());self.assertIs(report.get('success'),True);self.assertIs(report.get('private_services_stopped'),True)
  validate_report(report,json.loads(FIXTURE.read_text()))
if __name__=='__main__':unittest.main()
