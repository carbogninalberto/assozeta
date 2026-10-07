"""Exercise the actual JSON-RPC subprocess, not just Python tool functions."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path
from io import StringIO
from unittest.mock import AsyncMock, patch

from django.core.management import call_command
from django.test import SimpleTestCase

from django.db import connection
from django.conf import settings
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from application.tests.base import BaseTransactionTestCase
from application.tests.fixtures.factories import create_test_sport_association, create_test_course


class MCPStdioCommandTests(SimpleTestCase):
    def test_startup_banner_never_writes_to_protocol_stdout(self):
        stdout, stderr = StringIO(), StringIO()
        association_id = '00000000-0000-0000-0000-000000000001'
        with patch('application.mcp_server.server.run_stdio_server', new_callable=AsyncMock) as run_server:
            call_command('run_mcp_server', association_id=association_id,
                         transport='stdio', stdout=stdout, stderr=stderr)
        run_server.assert_awaited_once_with(association_id)
        self.assertEqual(stdout.getvalue(), '')
        self.assertIn('Starting MCP server (stdio)', stderr.getvalue())


class MCPStdioTests(BaseTransactionTestCase):
    def test_manual_retrieval_citations_and_public_evidence_boundary_over_existing_transport(self):
        from application.manuale.index import build_index, digest, file_digest, promote, sections
        association = create_test_sport_association()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'BE').mkdir()
            (root / 'docs').mkdir()
            source = root / 'BE/guide.py'
            source.write_text('def assign_tag():\n    return True\n')
            page = root / 'docs/tag.mdx'
            page.write_text('## Assegnare un tag\nSeleziona il tesserato e premi Assegna Tag.\n')
            manifest = {'metadata': {'application_revision': 'manual-test', 'manual_revision': 'manual-source-test',
                                     'release': settings.RUNNING_VERSION, 'manual_url': 'https://manual.example'},
                        'pages': [{'path': 'docs/tag.mdx', 'sections': [{'id': 'assegnare-un-tag', 'status': 'verified',
                            'content_sha256': digest(sections(page.read_text())['assegnare-un-tag']['mdx']),
                            'evidence': [{'path': 'BE/guide.py', 'start': 1, 'end': 2, 'symbol': 'assign_tag', 'sha256': file_digest(source)}]}]}]}
            corpus = root / 'index.json'
            promote(build_index(root, root, manifest), corpus)
            params = StdioServerParameters(command=sys.executable,
                args=['manage.py', 'run_mcp_server', '--association-id', str(association.pk)],
                env={**os.environ, 'DBNAME': connection.settings_dict['NAME'],
                     'MANUAL_INDEX_PATH': str(corpus), 'MANUAL_APPLICATION_REVISION': 'manual-test', 'MANUAL_SOURCE_ROOT': ''})

            async def exercise():
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        retrieved = await session.call_tool('search_manual', {'query': 'assegnare tag'})
                        result = json.loads(retrieved.content[0].text)
                        self.assertEqual(result['status'], 'verified')
                        self.assertEqual(result['results'][0]['url'], 'https://manual.example/docs/tag#assegnare-un-tag')
                        self.assertNotIn('evidence', result['results'][0])
                        evidence = await session.call_tool('get_manual_evidence', {'section_id': 'docs/tag#assegnare-un-tag'})
                        self.assertEqual(json.loads(evidence.content[0].text)['status'], 'forbidden')
                        missing = await session.call_tool('search_manual', {'query': 'teletrasporto satellitare'})
                        self.assertEqual(json.loads(missing.content[0].text)['status'], 'no_evidence')
                        # Existing association-scoped data access still works after manual tools.
                        count = await session.call_tool('count_data', {'model_name': 'Course'})
                        self.assertEqual(json.loads(count.content[0].text), {'count': 0})

            asyncio.run(asyncio.wait_for(exercise(), timeout=45))

    def test_handshake_and_tenant_scoped_orm_queries(self):
        association = create_test_sport_association()
        course = create_test_course(sport_association=association, title='Visible')
        create_test_course(title='Private other tenant')
        params = StdioServerParameters(command=sys.executable,
            args=['manage.py', 'run_mcp_server', '--association-id', str(association.pk)],
            env={**os.environ, 'DBNAME':connection.settings_dict['NAME']})

        async def exercise():
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    self.assertEqual(len(tools.tools), 15)
                    self.assertTrue({'search_manual', 'get_manual_section', 'get_manual_evidence', 'get_manual_gaps'}.issubset({tool.name for tool in tools.tools}))
                    gaps = json.loads((await session.call_tool('get_manual_gaps', {})).content[0].text)
                    self.assertEqual(gaps['status'], 'forbidden')
                    for _ in range(2):
                        result = await session.call_tool('count_data', {'model_name':'Course'})
                        self.assertEqual(json.loads(result.content[0].text), {'count':1})
                    result = await session.call_tool('query_data', {'model_name':'Course', 'fields':['course_id', 'title']})
                    self.assertEqual(json.loads(result.content[0].text)['data'], [{'course_id':str(course.pk), 'title':'Visible'}])
                    # Cover both read and write ORM tools through the real async transport.
                    result = await session.call_tool('save_report', {
                        'name': 'MCP regression', 'tool_name': 'query_data',
                        'params': {'model_name': 'Course'}, 'user_id': str(association.user_id),
                    })
                    saved = json.loads(result.content[0].text)
                    self.assertIn('saved_report_id', saved)
                    result = await session.call_tool('list_reports', {'user_id': str(association.user_id)})
                    reports = json.loads(result.content[0].text)['reports']
                    self.assertEqual([report['saved_report_id'] for report in reports], [saved['saved_report_id']])
                    result = await session.call_tool('count_data', {'model_name':'NotAllowed'})
                    self.assertIn('error', json.loads(result.content[0].text))
                    # A failed tool must leave the protocol usable.
                    result = await session.call_tool('count_data', {'model_name':'Course'})
                    self.assertEqual(json.loads(result.content[0].text)['count'], 1)

        asyncio.run(asyncio.wait_for(exercise(), timeout=45))
