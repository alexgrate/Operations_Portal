from django.shortcuts import render, redirect
# from django.contrib.auth models import user
from django.contrib.auth import authenticate, login, logout


# posts = [
#     {
#         'author': 'coreyMS',
#         'title': 'Portal Post',
#         'content': 'First post content',
#         'date_posted': 'August 27, 2018'
#     },
#     {
#         'author': 'coreyMS',
#         'title': 'Portal Post 2',
#         'content': 'second post content',
#         'date_posted': 'August 28, 2018'
#     },
#     {
#         'author': 'coreyMS',
#         'title': 'Portal Post 3',
#         'content': 'third post content',
#         'date_posted': 'August 29, 2018'
#     }
# ]


# Create your views here.
def signup(request):
    if request.method == 'POST':
        firt_name = request.POST['first_name']
        last_name = request.POST['last_name']
        email = request.POST['email']
        password = request.POST['password']
        password2 = request.POST['password2']

        if password == password2:
            if user.objects.filter(email=email).exists():
                message.info(request, 'Email taken')
                return redirect('signup')
            else:
                user = User.objects.create_user(email=email, password=password, first_name=first_name, last_name=last_name)
                user.save()
                message.success(request, 'Account created successfully')
                return redirect('login')
        else:
            message.info(request, 'Passwords do not match')
            return redirect(request, 'signup')
    return redirect(request, 'login.html')

             

def login(request):
    return render(request, 'portal/login.html')

def dashboard(request):
    return render(request, 'portal/dashboard.html')
# {'title': 'About'}
