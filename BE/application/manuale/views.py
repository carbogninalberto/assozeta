"""Authenticated embedded reader over the same compatible corpus as MCP."""
from io import BytesIO

from django.http import FileResponse, Http404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from core.middleware import IsAuthenticated
from .index import digest, owned_path
from .tools import _load, _public, _unavailable
from .preview import load_preview, preview_results


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def manual_sections(request):
    try:
        query = request.query_params.get('query', '').strip()
        if len(query) > 2000:
            return Response({'detail': 'La ricerca è troppo lunga.'}, status=400)
        preview = load_preview()
        if preview:
            return Response(preview_results(preview[0], query))
        # The reader exposes ordinary documentation even to owners. Technical
        # evidence remains behind the existing maintainer-only MCP tool.
        index, context = _load(None)
        chunks = index.search(query, limit=10, **context) if query else index.applicable(**context)
        results = []
        for chunk in chunks:
            section = _public(chunk, index)
            section['url'] = section['embedded_url']
            results.append(section)
        return Response({'status': 'verified' if results else 'no_evidence', 'results': results,
                         'message': '' if results else 'Non ho trovato istruzioni verificate per questa domanda.'})
    except (OSError, ValueError, KeyError, TypeError):
        return Response(_unavailable(None))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def manual_asset(request, asset_path):
    try:
        if asset_path.startswith('preview/'):
            preview = load_preview()
            if not preview:
                raise Http404
            package, root = preview
            relative = asset_path.removeprefix('preview/')
            image = next((image for chunk in package['sections'] for image in chunk['screenshots']
                          if image['path'] == relative), None)
            if not image:
                raise Http404
            contents = owned_path(root, relative).read_bytes()
            if not relative.endswith('.png') or digest(contents) != image['sha256']:
                raise Http404
            response = FileResponse(BytesIO(contents), content_type='image/png')
            response['Cache-Control'] = 'private, no-store'
            response['X-Content-Type-Options'] = 'nosniff'
            return response
        index, context = _load(None)
        if not index.asset_root:
            raise Http404
        image = next((image for chunk in index.applicable(**context) for image in chunk['screenshots']
                      if image['path'] == asset_path), None)
        if not image:
            raise Http404
        if getattr(index, 'asset_content_addressed', False):
            from .sync import cached_asset_path
            path = cached_asset_path(image)
        else:
            path = owned_path(index.asset_root, asset_path)
        # Read once so replacement between verification and streaming cannot
        # return different bytes from those proven by the capture report.
        contents = path.read_bytes()
        if path.suffix.lower() != '.png' or digest(contents) != image['sha256']:
            raise Http404
        response = FileResponse(BytesIO(contents), content_type='image/png')
        response['Cache-Control'] = 'private, no-store'
        response['X-Content-Type-Options'] = 'nosniff'
        return response
    except (OSError, ValueError, KeyError, TypeError):
        raise Http404 from None
