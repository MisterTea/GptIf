from dotenv import load_dotenv

load_dotenv()  # take environment variables from .env.

import base64
import contextvars
import json
import os
import uuid
from typing import Annotated, Any, Dict, List, Optional, Tuple, Union, cast

import nacl
import nacl.secret
import nacl.utils

from gptif.console import ConsoleHandler, session_id_contextvar
from gptif.state import World

from fastapi.responses import RedirectResponse

from fastapi import APIRouter, Cookie, Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import Response
from jose import jwt
from pydantic import BaseModel
from starlette import status
from starlette.responses import HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import gptif.handle_input
from gptif.db import (
    AiImage,
    GameState,
    GptDialogue,
    create_db_and_tables,
    get_ai_image_from_id,
    get_ai_image_if_cached,
    get_answer_if_cached,
    get_game_state_from_id,
    put_ai_image_in_cache,
    put_answer_in_cache,
    upsert_game_state,
)
from gptif.llm import LlamaCppLanguageModel, OpenAiLanguageModel

stage = os.environ.get("STAGE", None)
root_path = f"/{stage}/" if stage else "/"

app = FastAPI(title="MyAwesomeApp", root_path=root_path)

origins = [
    "http://gptif-site.s3-website-us-east-1.amazonaws.com",
    "https://gptif-site.s3-website-us-east-1.amazonaws.com",
    "http://generativefiction.com",
    "https://generativefiction.com",
    "http://localhost",
    "http://localhost:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

openai_model = OpenAiLanguageModel()

secret_key = base64.b64decode(os.environ["GPTIF_SECRET_KEY"])
secret_box = nacl.secret.SecretBox(secret_key)


class GameCommand(BaseModel):
    command: str


@app.on_event("startup")
def on_startup():
    create_db_and_tables()


def fetch_session_id(
    session_cookie: Annotated[Union[str, None], Cookie()] = None,
) -> Optional[str]:
    print("FETCHING SESSION ID")
    print(session_cookie)
    print(type(session_cookie))
    if session_cookie is None:
        return None
    session_cookie_bytes = base64.b64decode(session_cookie)
    session_cookie_decrypted = json.loads(
        secret_box.decrypt(session_cookie_bytes).decode()
    )
    print(session_cookie_decrypted)
    session_id = session_cookie_decrypted.get(
        "logged_in_id", session_cookie_decrypted["logged_out_id"]
    )
    print(session_id)
    return session_id


@app.post("/api/fetch_dialogue")
async def fetch_dialogue(query: GptDialogue) -> str:
    answer = get_answer_if_cached(query)
    if answer is None and query.model_version == openai_model.model_name():
        # Grab the answer from openai
        assert query.stop_words is not None
        answer = openai_model.llm(
            query.context, stop=query.stop_words.split(","), echo=False
        )
        query.answer = answer
        put_answer_in_cache(query)
        return answer

    return "None" if answer is None else answer


@app.post("/api/fetch_image_id_for_caption")
async def fetch_image_id_for_caption(query: AiImage) -> str:
    ai_image = get_ai_image_if_cached(query)
    if ai_image is None:
        # Grab the ai_image from openai
        import openai

        response = openai.Image.create(
            prompt=query.prompt,
            n=1,
            size="256x256",
            response_format="b64_json",
        )

        image_data_b64 = response["data"][0]["b64_json"]
        image_data_bytes = base64.b64decode(image_data_b64)
        query.result = image_data_bytes

        put_ai_image_in_cache(query)

        assert query.id is not None

        return str(query.id)
    return str(ai_image.id)


@app.get(
    "/api/ai_image/{image_id}",
    responses={200: {"content": {"image/png": {}}}},
    # Prevent FastAPI from adding "application/json" as an additional
    # response media type in the autogenerated OpenAPI specification.
    # https://github.com/tiangolo/fastapi/issues/3258
    response_class=Response,
)
async def ai_image(image_id: str) -> Response:
    int_id = int(image_id)
    ai_image = get_ai_image_from_id(int_id)
    if ai_image is None:
        raise Exception("Oops")
    return Response(content=ai_image.result, media_type="image/png")


@app.post("/api/put_dialogue")
async def put_dialogue(query: GptDialogue):
    put_answer_in_cache(query)


@app.get("/")
async def send_to_index():
    return RedirectResponse("index.html")


@app.get("/api")
async def root():
    return {"message": "Hello World!"}


@app.post("/api/begin_game")
async def begin_game() -> JSONResponse:
    session_id = str(uuid.uuid4())
    session_cookie = json.dumps({"logged_out_id": session_id})
    encrypted_session_cookie = base64.b64encode(
        secret_box.encrypt(session_cookie.encode("utf-8"))
    ).decode()
    print("SESSION ID", session_id)
    session_id_contextvar.set(session_id)
    print(gptif.console.console.buffers.get(session_id, []))

    world = World()
    print("NEW GAME")
    game_state = GameState(session_id=session_id, version=world.version)
    world.start_chapter_one()
    world.save(game_state)
    print("NEW GAME STATE")
    print(game_state)
    upsert_game_state(game_state)
    response = JSONResponse(content=gptif.console.console.buffers.get(session_id, []))
    if session_id in gptif.console.console.buffers:
        del gptif.console.console.buffers[session_id]
    session_id_contextvar.set("")
    response.set_cookie("session_cookie", encrypted_session_cookie)
    return response


@app.post("/api/handle_input")
async def handle_input(
    command: GameCommand, session_id=Depends(fetch_session_id)
) -> JSONResponse:
    print("IN POST")
    print(session_id)
    if session_id is None:
        raise HTTPException(status_code=400, detail="Sent input but there's no game")
    assert session_id is not None
    print("SESSION ID", session_id)
    session_id_contextvar.set(session_id)
    print(gptif.console.console.buffers.get(session_id, []))

    world = World()
    game_state = get_game_state_from_id(session_id)
    assert game_state is not None, "Missing game state"
    if not world.load(game_state):
        assert False
        # raise HTTPException(status_code=400, detail="Incompatible world version")
        world.start_chapter_one()
    gptif.handle_input.handle_input(world, command.command)
    world.save(game_state)
    print("NEW GAME STATE")
    print(game_state)
    upsert_game_state(game_state)
    response = JSONResponse(content=gptif.console.console.buffers.get(session_id, []))
    print("BEFORE AND AFTER")
    print(gptif.console.console.buffers.get(session_id, []))
    if session_id in gptif.console.console.buffers:
        del gptif.console.console.buffers[session_id]
    print(gptif.console.console.buffers.get(session_id, []))
    session_id_contextvar.set("")
    return response
