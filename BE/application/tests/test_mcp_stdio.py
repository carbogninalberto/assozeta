"""Exercise the actual JSON-RPC subprocess, not just Python tool functions."""
import asyncio
import json
import os
import sys

from django.db import connection
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from application.tests.base import BaseTransactionTestCase
from application.tests.fixtures.factories import create_test_sport_association, create_test_course


class MCPStdioTests(BaseTransactionTestCase):
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
                    self.assertEqual(len(tools.tools), 11)
                    for _ in range(2):
                        result = await session.call_tool('count_data', {'model_name':'Course'})
                        self.assertEqual(json.loads(result.content[0].text), {'count':1})
                    result = await session.call_tool('query_data', {'model_name':'Course', 'fields':['course_id', 'title']})
                    self.assertEqual(json.loads(result.content[0].text)['data'], [{'course_id':str(course.pk), 'title':'Visible'}])
                    result = await session.call_tool('count_data', {'model_name':'NotAllowed'})
                    self.assertIn('error', json.loads(result.content[0].text))
                    # A failed tool must leave the protocol usable.
                    result = await session.call_tool('count_data', {'model_name':'Course'})
                    self.assertEqual(json.loads(result.content[0].text)['count'], 1)

        asyncio.run(asyncio.wait_for(exercise(), timeout=45))
