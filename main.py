import os
import re
from datetime import datetime, timezone
from typing import Optional, Tuple

import google.oauth2.id_token
from azure.storage.blob import BlobServiceClient
from azure.storage.blob import ContentSettings
from azure.core.exceptions import AzureError
from bson import ObjectId
from fastapi import FastAPI
from fastapi import File
from fastapi import Form
from fastapi import UploadFile
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
from fastapi.responses import Response
from google.auth.transport import requests
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database
from dotenv import load_dotenv

app = FastAPI()
FIREBASE_PROJECT_ID = "twitter-replica-fccbb"
load_dotenv()
MONGODB_URI = os.getenv("MONGODB_URI", "")
STUDENT_NUMBER = os.getenv("STUDENT_NUMBER", "XXXXXXX")
DATABASE_NAME = f"A2-{STUDENT_NUMBER}"
AZURE_STORAGE_CONNECTION_STRING = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "UseDevelopmentStorage=true")
AZURE_STORAGE_CONTAINER_NAME = os.getenv("AZURE_STORAGE_CONTAINER_NAME", "profile-images")
AZURITE_FULL_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)

firebase_request_adapter = requests.Request()
mongo_client: Optional[MongoClient] = MongoClient(MONGODB_URI) if MONGODB_URI else None
database: Optional[Database] = mongo_client[DATABASE_NAME] if mongo_client else None
users_collection: Optional[Collection] = None
tweets_collection: Optional[Collection] = None
blob_service_client: Optional[BlobServiceClient] = None

app.mount('/static', StaticFiles(directory='static'), name='static')
templates = Jinja2Templates(directory='templates')


def build_blob_service_client() -> Optional[BlobServiceClient]:
    normalized_connection_string = AZURE_STORAGE_CONNECTION_STRING.strip()
    if normalized_connection_string == "UseDevelopmentStorage=true":
        normalized_connection_string = AZURITE_FULL_CONNECTION_STRING

    connection_strings_to_try = [normalized_connection_string, AZURITE_FULL_CONNECTION_STRING]
    attempted_values = set()

    for connection_string in connection_strings_to_try:
        if not connection_string:
            continue
        if connection_string in attempted_values:
            continue
        attempted_values.add(connection_string)

        try:
            return BlobServiceClient.from_connection_string(connection_string)
        except (AzureError, ValueError):
            continue

    return None


def ensure_required_collections() -> None:
    if database is None:
        return

    existing_collections = set(database.list_collection_names())
    if "User" not in existing_collections:
        database.create_collection("User")
    if "Tweet" not in existing_collections:
        database.create_collection("Tweet")


def get_user_token_from_cookie(request: Request) -> Tuple[Optional[dict], Optional[str]]:
    id_token = request.cookies.get("token")
    if not id_token:
        return None, "Please sign in to continue"

    try:
        user_token = google.oauth2.id_token.verify_firebase_token(
            id_token,
            firebase_request_adapter,
            FIREBASE_PROJECT_ID
        )
        return user_token, None
    except ValueError as err:
        return None, str(err)


def get_optional_user_token_from_cookie(request: Request) -> Tuple[Optional[dict], Optional[str]]:
    id_token = request.cookies.get("token")
    if not id_token:
        return None, None

    try:
        user_token = google.oauth2.id_token.verify_firebase_token(
            id_token,
            firebase_request_adapter,
            FIREBASE_PROJECT_ID
        )
        return user_token, None
    except ValueError as err:
        return None, str(err)


def get_or_create_current_user(user_token: dict) -> Optional[dict]:
    if users_collection is None:
        return None

    firebase_uid = user_token.get("user_id") or user_token.get("sub")
    if not firebase_uid:
        return None

    existing_user = users_collection.find_one({"firebase_uid": firebase_uid})
    if existing_user:
        return existing_user

    users_collection.insert_one(
        {
            "firebase_uid": firebase_uid,
            "email": user_token.get("email"),
            "username": None,
            "profile_image_url": None,
            "follower_user_ids": [],
            "following_user_ids": [],
            "created_at": datetime.now(timezone.utc),
        }
    )
    return users_collection.find_one({"firebase_uid": firebase_uid})


def get_tweets_for_user(user_id) -> list[dict]:
    if tweets_collection is None:
        return []

    return list(
        tweets_collection.find({"user_id": user_id}).sort("created_at", -1)
    )


def get_latest_tweets_for_user(user_id, limit: int) -> list[dict]:
    if tweets_collection is None:
        return []

    return list(
        tweets_collection.find({"user_id": user_id}).sort("created_at", -1).limit(limit)
    )


def get_timeline_tweets_for_user(current_user: dict, limit: int = 20) -> list[dict]:
    if tweets_collection is None:
        return []

    following_user_ids = current_user.get("following_user_ids", [])
    timeline_user_ids = [current_user["_id"], *following_user_ids]
    return list(
        tweets_collection.find({"user_id": {"$in": timeline_user_ids}})
        .sort("created_at", -1)
        .limit(limit)
    )


def get_following_users(current_user: dict) -> list[dict]:
    if users_collection is None:
        return []

    following_user_ids = current_user.get("following_user_ids", [])
    if not following_user_ids:
        return []

    return list(
        users_collection.find({"_id": {"$in": following_user_ids}})
        .sort("username", 1)
    )


def search_users_by_username_prefix(username_query: str) -> list[dict]:
    if users_collection is None or not username_query:
        return []

    query_pattern = f"^{re.escape(username_query)}"
    return list(
        users_collection.find(
            {"username": {"$regex": query_pattern, "$options": "i"}}
        )
        .sort("username", 1)
        .limit(20)
    )


def search_tweets_by_text_prefix(tweet_query: str) -> list[dict]:
    if tweets_collection is None or not tweet_query:
        return []

    query_pattern = f"^{re.escape(tweet_query)}"
    return list(
        tweets_collection.find(
            {"text": {"$regex": query_pattern, "$options": "i"}}
        )
        .sort("created_at", -1)
        .limit(20)
    )


def sanitize_filename(filename: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "-", filename)


def build_profile_image_blob_name(user_id: ObjectId, original_filename: str) -> str:
    safe_name = sanitize_filename(original_filename or "profile-image")
    return f"profile/{user_id}-{safe_name}"


def upload_profile_image_to_blob(
    user_id: ObjectId,
    file_name: str,
    content: bytes,
    content_type: str,
) -> tuple[str, str]:
    if blob_service_client is None:
        raise RuntimeError("Blob storage is not configured.")

    blob_name = build_profile_image_blob_name(user_id, file_name)
    blob_client = blob_service_client.get_blob_client(
        container=AZURE_STORAGE_CONTAINER_NAME,
        blob=blob_name,
    )
    blob_client.upload_blob(
        content,
        overwrite=True,
        content_settings=ContentSettings(content_type=content_type),
    )
    return blob_client.url, blob_name


def download_profile_image_blob(blob_name: str) -> tuple[bytes, str]:
    if blob_service_client is None:
        raise RuntimeError("Blob storage is not configured.")

    blob_client = blob_service_client.get_blob_client(
        container=AZURE_STORAGE_CONTAINER_NAME,
        blob=blob_name,
    )
    downloader = blob_client.download_blob()
    properties = blob_client.get_blob_properties()
    media_type = "application/octet-stream"
    if properties.content_settings and properties.content_settings.content_type:
        media_type = properties.content_settings.content_type
    return downloader.readall(), media_type


def render_home(
    request: Request,
    error_message: Optional[str] = None,
    success_message: Optional[str] = None,
) -> HTMLResponse:
    user_token, token_error = get_user_token_from_cookie(request)
    current_user = None
    timeline_tweets = []
    following_users = []
    username_query = request.query_params.get("username_query", "").strip()
    tweet_query = request.query_params.get("tweet_query", "").strip()
    matched_users = []
    matched_tweets = []

    if mongo_client is None or database is None:
        error_message = "MongoDB is not configured."
    elif users_collection is None or tweets_collection is None:
        error_message = "MongoDB connection failed."
    elif user_token:
        current_user = get_or_create_current_user(user_token)
        if current_user:
            timeline_tweets = get_timeline_tweets_for_user(current_user, 20)
            following_users = get_following_users(current_user)
            matched_users = search_users_by_username_prefix(username_query)
            matched_tweets = search_tweets_by_text_prefix(tweet_query)

    if not error_message and token_error:
        error_message = token_error

    return templates.TemplateResponse(
        "main.html",
        {
            "request": request,
            "user_token": user_token,
            "error_message": error_message,
            "success_message": success_message,
            "database_name": DATABASE_NAME,
            "current_user": current_user,
            "needs_username": bool(current_user) and not current_user.get("username"),
            "timeline_tweets": timeline_tweets,
            "following_users": following_users,
            "username_query": username_query,
            "tweet_query": tweet_query,
            "matched_users": matched_users,
            "matched_tweets": matched_tweets,
        },
    )


def render_profile(
    request: Request,
    profile_username: str,
    error_message: Optional[str] = None,
    success_message: Optional[str] = None,
) -> HTMLResponse:
    current_user = None
    profile_user = None
    profile_tweets = []
    can_follow = False
    is_following = False
    token_error = None

    if users_collection is None or tweets_collection is None:
        error_message = "MongoDB connection failed."
    else:
        user_token, token_error = get_optional_user_token_from_cookie(request)
        if user_token:
            current_user = get_or_create_current_user(user_token)
        profile_user = users_collection.find_one({"username": profile_username})
        if profile_user:
            profile_tweets = get_latest_tweets_for_user(profile_user["_id"], 10)
            if current_user and current_user["_id"] != profile_user["_id"] and current_user.get("username"):
                can_follow = True
                following_ids = current_user.get("following_user_ids", [])
                is_following = profile_user["_id"] in following_ids
        elif not error_message:
            error_message = "Profile not found."

    if not error_message and token_error:
        error_message = token_error

    return templates.TemplateResponse(
        "profile.html",
        {
            "request": request,
            "error_message": error_message,
            "success_message": success_message,
            "current_user": current_user,
            "profile_user": profile_user,
            "profile_tweets": profile_tweets,
            "can_follow": can_follow,
            "is_following": is_following,
        },
    )


@app.on_event("startup")
async def startup_event() -> None:
    global users_collection
    global tweets_collection
    global blob_service_client

    ensure_required_collections()
    if database is not None:
        users_collection = database.get_collection("User")
        tweets_collection = database.get_collection("Tweet")

    blob_service_client = build_blob_service_client()
    if blob_service_client is not None:
        try:
            blob_service_client.create_container(AZURE_STORAGE_CONTAINER_NAME)
        except AzureError:
            pass


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return render_home(request)


@app.get("/profile/{username}", response_class=HTMLResponse)
async def profile_page(request: Request, username: str):
    return render_profile(request, username)


@app.get("/profile/{username}/picture")
async def profile_picture(username: str):
    if users_collection is None:
        return Response(status_code=404)

    profile_user = users_collection.find_one({"username": username})
    if not profile_user:
        return Response(status_code=404)

    blob_name = profile_user.get("profile_image_blob_name")
    if not blob_name:
        return Response(status_code=404)

    try:
        image_bytes, media_type = download_profile_image_blob(blob_name)
    except (RuntimeError, AzureError):
        return Response(status_code=404)

    return Response(content=image_bytes, media_type=media_type)


@app.post("/set-username", response_class=HTMLResponse)
async def set_username(request: Request, username: str = Form(...)):
    user_token, token_error = get_user_token_from_cookie(request)
    if token_error:
        return render_home(request, error_message=token_error)

    if users_collection is None:
        return render_home(request, error_message="MongoDB connection failed.")

    clean_username = username.strip()
    if not clean_username:
        return render_home(request, error_message="Username is required.")

    existing_username = users_collection.find_one({"username": clean_username})
    if existing_username:
        return render_home(request, error_message="Username already exists. Please choose another.")

    current_user = get_or_create_current_user(user_token)
    if not current_user:
        return render_home(request, error_message="Unable to find current user.")

    users_collection.update_one(
        {"_id": current_user["_id"]},
        {"$set": {"username": clean_username}}
    )
    return render_home(request, success_message="Username saved.")


@app.post("/add-tweet", response_class=HTMLResponse)
async def add_tweet(request: Request, tweet_text: str = Form(...)):
    user_token, token_error = get_user_token_from_cookie(request)
    if token_error:
        return render_home(request, error_message=token_error)

    if users_collection is None or tweets_collection is None:
        return render_home(request, error_message="MongoDB connection failed.")

    current_user = get_or_create_current_user(user_token)
    if not current_user:
        return render_home(request, error_message="Unable to find current user.")

    if not current_user.get("username"):
        return render_home(request, error_message="Set a username before posting tweets.")

    clean_tweet = tweet_text.strip()
    if not clean_tweet:
        return render_home(request, error_message="Tweet cannot be empty.")
    if len(clean_tweet) > 280:
        return render_home(request, error_message="Tweet must be 280 characters or fewer.")

    tweets_collection.insert_one(
        {
            "user_id": current_user["_id"],
            "username": current_user["username"],
            "text": clean_tweet,
            "created_at": datetime.now(timezone.utc),
        }
    )
    return render_home(request, success_message="Tweet posted.")


@app.post("/profile/{username}/toggle-follow", response_class=HTMLResponse)
async def toggle_follow(request: Request, username: str):
    user_token, token_error = get_user_token_from_cookie(request)
    if token_error:
        return render_profile(request, username, error_message=token_error)

    if users_collection is None:
        return render_profile(request, username, error_message="MongoDB connection failed.")

    current_user = get_or_create_current_user(user_token)
    if not current_user:
        return render_profile(request, username, error_message="Unable to find current user.")
    if not current_user.get("username"):
        return render_profile(request, username, error_message="Set a username before following users.")

    profile_user = users_collection.find_one({"username": username})
    if not profile_user:
        return render_profile(request, username, error_message="Profile not found.")
    if profile_user["_id"] == current_user["_id"]:
        return render_profile(request, username, error_message="You cannot follow yourself.")

    following_ids = current_user.get("following_user_ids", [])
    is_following = profile_user["_id"] in following_ids
    if is_following:
        users_collection.update_one(
            {"_id": current_user["_id"]},
            {"$pull": {"following_user_ids": profile_user["_id"]}},
        )
        users_collection.update_one(
            {"_id": profile_user["_id"]},
            {"$pull": {"follower_user_ids": current_user["_id"]}},
        )
        return render_profile(request, username, success_message="User unfollowed.")

    users_collection.update_one(
        {"_id": current_user["_id"]},
        {"$addToSet": {"following_user_ids": profile_user["_id"]}},
    )
    users_collection.update_one(
        {"_id": profile_user["_id"]},
        {"$addToSet": {"follower_user_ids": current_user["_id"]}},
    )
    return render_profile(request, username, success_message="User followed.")


@app.post("/unfollow/{username}", response_class=HTMLResponse)
async def unfollow_user(request: Request, username: str):
    user_token, token_error = get_user_token_from_cookie(request)
    if token_error:
        return render_home(request, error_message=token_error)

    if users_collection is None:
        return render_home(request, error_message="MongoDB connection failed.")

    current_user = get_or_create_current_user(user_token)
    if not current_user:
        return render_home(request, error_message="Unable to find current user.")

    profile_user = users_collection.find_one({"username": username})
    if not profile_user:
        return render_home(request, error_message="User not found.")
    if profile_user["_id"] == current_user["_id"]:
        return render_home(request, error_message="You cannot unfollow yourself.")

    users_collection.update_one(
        {"_id": current_user["_id"]},
        {"$pull": {"following_user_ids": profile_user["_id"]}},
    )
    users_collection.update_one(
        {"_id": profile_user["_id"]},
        {"$pull": {"follower_user_ids": current_user["_id"]}},
    )
    return render_home(request, success_message=f"Unfollowed @{username}.")


@app.post("/profile/{username}/upload-picture", response_class=HTMLResponse)
async def upload_profile_picture(request: Request, username: str, profile_image: UploadFile = File(...)):
    user_token, token_error = get_user_token_from_cookie(request)
    if token_error:
        return render_profile(request, username, error_message=token_error)

    if users_collection is None:
        return render_profile(request, username, error_message="MongoDB connection failed.")

    current_user = get_or_create_current_user(user_token)
    if not current_user:
        return render_profile(request, username, error_message="Unable to find current user.")
    if current_user.get("username") != username:
        return render_profile(request, username, error_message="You can only change your own profile picture.")

    file_name = profile_image.filename or ""
    lowered_name = file_name.lower()
    allowed_extensions = (".jpg", ".jpeg", ".png")
    allowed_content_types = {"image/jpeg", "image/png"}
    if not lowered_name.endswith(allowed_extensions) or profile_image.content_type not in allowed_content_types:
        return render_profile(request, username, error_message="Only JPG and PNG images are allowed.")

    image_bytes = await profile_image.read()
    if not image_bytes:
        return render_profile(request, username, error_message="Uploaded file is empty.")

    try:
        _, blob_name = upload_profile_image_to_blob(
            current_user["_id"],
            file_name,
            image_bytes,
            profile_image.content_type or "application/octet-stream",
        )
    except (RuntimeError, AzureError) as err:
        return render_profile(
            request,
            username,
            error_message=f"Unable to upload image to Azurite storage: {err}",
        )

    users_collection.update_one(
        {"_id": current_user["_id"]},
        {
            "$set": {
                "profile_image_url": f"/profile/{username}/picture",
                "profile_image_blob_name": blob_name,
                "profile_image_updated_at": datetime.now(timezone.utc),
            }
        },
    )
    return render_profile(request, username, success_message="Profile picture updated.")