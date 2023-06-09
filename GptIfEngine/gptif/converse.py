import multiprocessing
import os
from typing import List, Optional

import requests
from rich.progress import Progress

import gptif.settings
from gptif import db
from gptif.console import console
from gptif.llm import llm
from gptif.state import Agent


def get_answer_from_cache(dialogue: db.GptDialogue) -> Optional[str]:
    if gptif.settings.CONVERSE_SERVER is not None:
        response = requests.post(
            gptif.settings.CONVERSE_SERVER + "/fetch_dialogue", json=dialogue.dict()
        )

        console.debug("RESPONSE", response)
        console.debug(response.content)

        # TODO: More gracefully handle errors
        assert response.status_code == 200

        x = response.content.decode()
        if x == "None" or x == '"None"':
            return None
        return x
    else:
        return db.get_answer_if_cached(dialogue)


def put_answer_in_cache(dialogue: db.GptDialogue):
    console.debug("PUTTING ANSWER IN CACHE")
    if gptif.settings.CONVERSE_SERVER is not None:
        response = requests.post(
            gptif.settings.CONVERSE_SERVER + "/put_dialogue", json=dialogue.dict()
        )

        # TODO: More gracefully handle errors
        assert response.status_code == 200
    else:
        db.put_answer_in_cache(dialogue)


def profile_for_agent(agent: Agent) -> str:
    return f"""
**Name:** {agent.profile.name}
**Age:** {agent.profile.age}
**Race:** {agent.profile.race}
**Gender:** {agent.profile.gender}
**Occupation:** {agent.profile.occupation}
**Personality:** {". ".join(agent.profile.personality)}
**Backstory:** {". ".join(agent.profile.backstory)}
**Goals:**   {". ".join(agent.profile.goals)}
**Notes:**   {". ".join(agent.notes)}
"""


def converse(target_agent: Agent, statement: str) -> Optional[str]:
    assert llm is not None

    assert target_agent.profile.personality is not None
    assert target_agent.profile.backstory is not None
    assert target_agent.profile.goals is not None

    context = f"""Given a character and question, answer the question in a paragraph.

Character:

{profile_for_agent(target_agent)}

Alfred: What is your name?
{target_agent.profile.name}: \"My name is {target_agent.profile.name}.\"

Alfred: {statement}
{target_agent.profile.name}: \""""

    assert target_agent.profile.name is not None

    dialogue = db.GptDialogue(
        character_name=target_agent.profile.name,
        model_version=llm.model_name(),
        question=statement,
        context=context,
        stop_words=",".join(["Alfred:", "\n"]),
    )

    assert dialogue.stop_words is not None

    cached_answer = get_answer_from_cache(dialogue)
    console.debug("Cached answer:", cached_answer)
    if cached_answer is not None:
        return cached_answer
    if gptif.settings.CLI_MODE:
        console.print(f"[purple]{target_agent.profile.name} thinks for a moment...[/]")
    while True:
        answer = llm.llm(
            dialogue.context, stop=dialogue.stop_words.split(","), echo=False
        )
        answer_text = answer
        if len(answer_text) > 0:
            put_answer_in_cache(
                db.GptDialogue(
                    character_name=dialogue.character_name,
                    model_version=dialogue.model_version,
                    question=dialogue.question,
                    context=dialogue.context,
                    answer=answer_text,
                    stop_words=dialogue.stop_words,
                )
            )
            return answer_text


def check_if_more_friendly(target_agent: Agent, statement: str) -> bool:
    assert llm is not None

    for friendly_question in target_agent.friend_questions:
        context = f"""Answer questions about the following statement:

\"{statement}\"

Is the statement above about chocolate?
No

{friendly_question}
"""

        assert target_agent.profile.name is not None

        dialogue = db.GptDialogue(
            character_name=target_agent.profile.name,
            model_version=llm.model_name(),
            question=statement,
            context=context,
            stop_words=",".join(["?", "\n\n"]),
        )
        assert dialogue.stop_words is not None

        cached_answer = get_answer_from_cache(dialogue)
        if cached_answer is not None:
            if "yes" in cached_answer.lower():
                return True
            else:
                assert "no" in cached_answer.lower(), cached_answer
                return False
        while True:
            answer = llm.llm(context, stop=dialogue.stop_words.split(","), echo=False)
            answer_text = answer
            console.debug("RAW ANSWER", answer_text)
            if "yes" in answer_text.lower():
                is_yes = True
            elif "no" in answer_text.lower():
                is_yes = False
            else:
                continue
            put_answer_in_cache(
                db.GptDialogue(
                    character_name=target_agent.profile.name,
                    model_version=llm.model_name(),
                    question=statement,
                    context=context,
                    answer=answer_text,
                    stop_words=dialogue.stop_words,
                )
            )
            if is_yes:
                return True
            break
    return False


def generate_fake_scenery(
    scenery_text: str, room_name: str, room_text: str
) -> Optional[str]:
    assert llm is not None

    context = f"""Given a room description and an object in the room, describe the object.
    
Room Name: {room_name}

Room Description: {room_text}
    
Object Name: {scenery_text}

Object Description: """

    dialogue = db.GptDialogue(
        character_name=None,
        model_version=llm.model_name(),
        question=scenery_text,
        context=context,
        stop_words=",".join(["?", "\n\n"]),
    )
    assert dialogue.stop_words is not None

    cached_answer = get_answer_from_cache(dialogue)
    if cached_answer is not None:
        return cached_answer
    answer = llm.llm(context, stop=dialogue.stop_words.split(","), echo=False)
    answer_text = answer
    console.debug("RAW ANSWER", answer_text)
    put_answer_in_cache(
        db.GptDialogue(
            character_name=None,
            model_version=llm.model_name(),
            question=scenery_text,
            context=context,
            answer=answer_text,
            stop_words=dialogue.stop_words,
        )
    )
    return answer_text


def describe_character(agent: Agent) -> str:
    assert llm is not None

    question = f"""Given a character profile, write a description of the character in a single paragraph. The description should include the age and race.
    
Character:

{profile_for_agent(agent)}

Description:

"""

    dialogue = db.GptDialogue(
        character_name=agent.name,
        model_version=llm.model_name(),
        question=question,
        context="",
    )

    cached_answer = get_answer_from_cache(dialogue)
    if cached_answer is not None:
        return cached_answer
    answer = llm.llm(question, echo=False)
    answer_text = answer
    console.debug("RAW ANSWER", answer_text)
    put_answer_in_cache(
        db.GptDialogue(
            character_name=dialogue.character_name,
            model_version=llm.model_name(),
            question=question,
            context="",
            answer=answer_text,
            stop_words=dialogue.stop_words,
        )
    )
    return answer_text
