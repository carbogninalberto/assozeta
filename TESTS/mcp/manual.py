"""Portable MCP transport checks against an installed release.

Pass a JSON object with command, args and manual expectations as argv[1]. The
adapter supplies the server command; this contract imports no backend code.
"""
import asyncio
import json
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def check(configuration):
    expected = configuration['manual']
    parameters = StdioServerParameters(command=configuration['command'], args=configuration['args'], env=dict(os.environ))
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {'query_data', 'count_data', 'search_manual', 'get_manual_section',
                    'get_manual_evidence', 'get_manual_gaps'}.issubset(names)
            async def call(name, arguments):
                response = await session.call_tool(name, arguments)
                assert not response.isError, name + ' returned a transport error'
                return json.loads(response.content[0].text)
            found = await call('search_manual', {'query': expected['query']})
            assert found['status'] == 'verified' and found['results']
            hit = next(section for section in found['results'] if section['id'] == expected['section_id'])
            for key in ('application_revision', 'corpus_identity'):
                assert hit[key] == expected[key], key
            assert hit['version'] == expected['release']
            assert hit['publication_status'] == 'release-bundled'
            section = await call('get_manual_section', {'section_id': hit['id']})
            assert section['section']['text'] == hit['text']
            assert (await call('get_manual_evidence', {'section_id': hit['id']}))['status'] == 'forbidden'
            assert (await call('get_manual_gaps', {}))['status'] == 'forbidden'
            assert (await call('search_manual', {'query': 'teletrasporto satellitare quantistico'}))['status'] == 'no_evidence'
            count = await call('count_data', {'model_name': 'Course'})
            assert count['count'] == configuration['course_count']
    return {'status': 'passed', 'transport': 'real-mcp-stdio', 'release': expected['release'],
            'corpus_identity': expected['corpus_identity'], 'public_permissions': True,
            'existing_data_tool': True, 'unsupported_abstained': True}


if __name__ == '__main__':
    print(json.dumps(asyncio.run(check(json.loads(sys.argv[1])))))
