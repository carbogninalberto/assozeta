from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def chat_test(request):
    """Test page for the AI agent WebSocket chat."""
    return render(request, 'chat/test.html')
