"""Portable manual reader HTTP contract; actual adapter observations are required."""
import json
import copy
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

def compare_reports(django,go):
 if not django.get('success') or not go.get('success'):raise AssertionError('Both actual HTTP runs must succeed')
 if set(django['cases'])!=set(go['cases']):raise AssertionError('Differential case set differs')
 for name,expected in django['cases'].items():
  actual=go['cases'][name]
  def normalized_body(value):
   value=copy.deepcopy(value)
   for section in value.get('results',[]):
    if 'corpus_identity' in section:section['corpus_identity']='<verified fixture publisher identity>'
   return value
  for key in ['status','body','headers','database_unchanged','writes','secret_free','publisher_requests','asset','context_selects']:
   left,right=expected.get(key),actual.get(key)
   if key=='body':left,right=normalized_body(left),normalized_body(right)
   if key=='headers':
    # DRF constructs Allow from a set; method order varies with Python's
    # process hash seed. Compare the complete method set, not its ordering.
    left,right=copy.deepcopy(left),copy.deepcopy(right)
    for headers in [left,right]:
     if headers.get('Allow'):headers['Allow']=', '.join(sorted(headers['Allow'].split(', ')))
   if left!=right:raise AssertionError(f'{name}.{key}: Django {left!r}, Go {right!r}')
  if actual['selects']>expected['selects']:raise AssertionError(f'{name}: Go SQL exceeds established Django budget')
class ManualReaderHTTPContracts(unittest.TestCase):
 def test_actual_observations(self):
  path=os.environ.get('TESTS_MANUAL_READER_HTTP_REPORT');self.assertTrue(path,'Real HTTP adapter report required')
  report=json.loads(Path(path).read_text());self.assertIs(report.get('success'),True);self.assertIs(report.get('private_services_stopped'),True)
  validate_report(report,json.loads(FIXTURE.read_text()))
 def test_complete_differential(self):
  paths=[os.environ.get(key) for key in ('TESTS_MANUAL_READER_HTTP_REPORT','TESTS_MANUAL_READER_GO_REPORT')]
  self.assertTrue(all(paths),'Both real HTTP reports are required')
  compare_reports(*(json.loads(Path(path).read_text()) for path in paths))
 def test_versioned_differential(self):
  paths=[os.environ.get(key) for key in ('TESTS_MANUAL_READER_V1_REPORT','TESTS_MANUAL_READER_V2_REPORT')]
  self.assertTrue(all(paths),'Both shipped Caddy versioned reports are required')
  reports=[json.loads(Path(path).read_text()) for path in paths]
  for report in reports:
   self.assertIs(report.get('versioned'),True);self.assertIs(report.get('private_services_stopped'),True)
   validate_report(report,json.loads(FIXTURE.read_text()))
  compare_reports(*reports)
if __name__=='__main__':unittest.main()
