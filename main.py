import os
import re
from datetime import datetime, timezone
from typing import Optional, Tuple

import google.oauth2.id_token
from fastapi import FastAPI
from fastapi import Form
from fastapi.requests import Request
from fastapi.responses import HTMLResponse
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

firebase_request_adapter = requests.Request()
mongo_client: Optional[MongoClient] = MongoClient(MONGODB_URI) if MONGODB_URI else None
database: Optional[Database] = mongo_client[DATABASE_NAME] if mongo_client else None
users_collection: Optional[Collection] = None
tweets_collection: Optional[Collection] = None

app.mount('/static', StaticFiles(directory='static'), name='static')
templates = Jinja2Templates(directory='templates')


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


def render_home(
    request: Request,
    error_message: Optional[str] = None,
    success_message: Optional[str] = None,
) -> HTMLResponse:
    user_token, token_error = get_user_token_from_cookie(request)
    current_user = None
    user_tweets = []
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
            user_tweets = get_tweets_for_user(current_user["_id"])
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
            "tweets": user_tweets,
            "username_query": username_query,
            "tweet_query": tweet_query,
            "matched_users": matched_users,
            "matched_tweets": matched_tweets,
        },
    )


@app.on_event("startup")
async def startup_event() -> None:
    global users_collection
    global tweets_collection

    ensure_required_collections()
    if database is not None:
        users_collection = database.get_collection("User")
        tweets_collection = database.get_collection("Tweet")


@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    return render_home(request)


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