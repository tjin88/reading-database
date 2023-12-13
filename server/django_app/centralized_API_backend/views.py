from django.shortcuts import render
from rest_framework import status, views
from rest_framework.response import Response
from .models import Manga
from .serializers import MangaSerializer
from django.contrib.auth import authenticate, login
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.models import User
import json
from rest_framework.decorators import api_view
from django.utils.decorators import method_decorator
from .models import Profile
from rest_framework.permissions import IsAuthenticated
from django.http import JsonResponse

class MangaCreateView(views.APIView):
    def post(self, request):
        serializer = MangaSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def get(self, request):
        mangas = Manga.objects.all()  # Retrieve all manga records from the database
        serializer = MangaSerializer(mangas, many=True)  # Serialize the data
        return Response(serializer.data)  # Return the serialized data in the response

class MangaUpdateView(views.APIView):
    def put(self, request, title):
        try:
            manga = Manga.objects.get(title=title)
            serializer = MangaSerializer(manga, data=request.data)
            if serializer.is_valid():
                serializer.save()
                return Response(serializer.data)
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        except Manga.DoesNotExist:
            return Response({'message': 'Manga not found'}, status=status.HTTP_404_NOT_FOUND)

class MangaSearchView(views.APIView):
    def get(self, request):
        title_query = request.GET.get('title', '')
        mangas = Manga.objects.filter(title__icontains=title_query)
        serializer = MangaSerializer(mangas, many=True)
        return Response(serializer.data)

@method_decorator(csrf_exempt, name='dispatch')
@api_view(['POST'])
def register_view(request):
    data = request.data
    username = data.get('email')
    password = data.get('password')
    profileName = data.get('profileName')

    try:
        if User.objects.get(username=username):
            return Response({"error": "User already exists"}, status=status.HTTP_409_CONFLICT)
    except User.DoesNotExist:
        # User does not exist, so we can create a new user
        user = User.objects.create_user(username=username, email=username, password=password, first_name=profileName)
        return Response({"message": "User created successfully", "user": {"username": user.username, "profileName": user.first_name}}, status=status.HTTP_201_CREATED)

    # This line should never be reached
    return Response({"error": "An unexpected error occurred"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@csrf_exempt
@api_view(['POST'])
def login_view(request):
    data = request.data
    username = data.get('email')
    password = data.get('password')
    user = authenticate(request, username=username, password=password)

    if user is not None:
        login(request, user)
        return Response({"message": "Login successful", "user": {"username": user.username, "profileName": user.first_name}}, status=status.HTTP_200_OK)
    else:
        return Response({"message": "Invalid credentials"}, status=status.HTTP_401_UNAUTHORIZED)
    
@api_view(['PUT'])
# @permission_classes([IsAuthenticated])
def update_reading_list(request):
    data = request.data
    email = data.get('username')

    # TODO: Get user authentication to work
    # if not user or user.is_anonymous:
    #     return Response({"error": "User not authenticated"}, status=status.HTTP_401_UNAUTHORIZED)

    try:
        user = User.objects.get(email=email)
        profile = Profile.objects.get(user=user)

        new_book = {
            'title': data.get('title'),
            'reading_status': data.get('reading_status'),
            'user_tag': data.get('user_tag'),
            'latest_read_chapter': data.get('latest_read_chapter'),
        }

        # Check if the book is already in the reading list
        if any(book['title'] == new_book['title'] for book in profile.reading_list):
            return Response({"error": "Book already in reading list"}, status=status.HTTP_400_BAD_REQUEST)

        # Add the new book to the reading list
        profile.reading_list.append(new_book)
        profile.save()

        return Response({"message": "Book added to reading list successfully"})
    except Profile.DoesNotExist:
        return Response({"error": "Profile not found"}, status=status.HTTP_404_NOT_FOUND)
    except User.DoesNotExist:
        return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

class UserProfileReadingListView(views.APIView):
    # TODO: Add tokens for user authentication
    # permission_classes = [IsAuthenticated]

    def get(self, request, email):
        try:
            user = User.objects.get(email=email)
            profile = Profile.objects.get(user=user)
            response = Response({'reading_list': profile.reading_list})
            # TODO: Handle CORS better. 
            # Currently I am allowing all origins, which must be changed before deploying
            response["Access-Control-Allow-Origin"] = "*"
            response["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            response["Access-Control-Allow-Headers"] = "Origin, Content-Type, Accept"
            return response
        except (User.DoesNotExist, Profile.DoesNotExist):
            return Response({'error': 'Profile not found'}, status=status.HTTP_404_NOT_FOUND)
