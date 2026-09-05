from django.shortcuts import render

# Create your views here.
def login_view(request):
    return render(request,'accounts/login.html')

def landing_view(request):
    return render(request, 'accounts/landing.html')
