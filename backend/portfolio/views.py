from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def workspace_view(request):
    return render(request, 'portfolio/workspace.html', {'active_module': 'portfolio'})
