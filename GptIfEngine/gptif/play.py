import os

from typing import Optional
from dotenv import load_dotenv

load_dotenv()  # take environment variables from .env.

import click
from gptif.converse import check_if_more_friendly, converse
from gptif.db import create_db_and_tables
from gptif.parser import (
    get_direct_object,
    get_verb_classes,
    handle_user_input,
    ParseException,
)
from gptif.console import console
from gptif.world import world
from rich.markdown import Markdown


class DummyContext(object):
    def __enter__(self):
        pass

    def __exit__(self, *args):
        pass


DIRECTION_VERBS = (
    "NORTH",
    "SOUTH",
    "EAST",
    "WEST",
    "UP",
    "DOWN",
    "N",
    "S",
    "E",
    "W",
    "U",
    "D",
)

DIRECTION_SHORT_LONG_MAP = {
    "N": "NORTH",
    "S": "SOUTH",
    "W": "WEST",
    "E": "EAST",
    "U": "UP",
    "D": "DOWN",
}


@click.command()
@click.option("--debug", default=False, is_flag=True)
@click.option("--converse-server", default=None)
@click.option("--sql-url", default=None)
def play(debug: bool, converse_server: Optional[str], sql_url: Optional[str]):
    if sql_url is not None:
        os.environ["SQL_URL"] = sql_url
    elif "SQL_URL" not in os.environ:
        os.environ["SQL_URL"] = "sqlite:///~/.gptif"

    if not debug:
        import gptif.console

        gptif.console.DEBUG_INPUT.clear()

    if converse_server is not None:
        import gptif.converse

        gptif.converse.RUN_LOCALLY = False
        gptif.converse.CONVERSE_SERVER = converse_server

    create_db_and_tables()

    with DummyContext():
        world.start_chapter_one()

        while True:
            while world.waiting_for_player is True:
                try:
                    command = console.get_input(">").strip()
                except KeyboardInterrupt as ki:
                    console.print()
                    console.print("[blue]Thanks for playing![/]")
                    return

                # Remove redundant whitespace
                command = " ".join(command.split())

                if len(command) == 0:
                    continue
                verb = command.split(" ")[0].upper()
                verb_classes = get_verb_classes(verb)
                if verb == "EXIT" or verb == "QUIT" or verb == "Q":
                    console.print()
                    console.print("[blue]Thanks for playing![/]")
                    return

                if verb not in DIRECTION_VERBS and len(verb_classes) == 0:
                    console.warning(
                        f"Sorry, I don't understand the verb {verb}. Remember that each sentence must begin with an action verb."
                    )
                    continue
                command_minus_verb = " ".join(command.split(" ")[1:])
                if verb in DIRECTION_VERBS or "51" in verb_classes:  # Go
                    if verb in DIRECTION_VERBS:
                        direction = verb
                    else:
                        direction = command_minus_verb
                        if direction not in DIRECTION_VERBS:
                            if len(direction) == 0:
                                console.warning(
                                    f"{verb} requires a direction (e.g. {verb} EAST)."
                                )
                            else:
                                console.warning(f"Invalid direction: {direction}")
                            continue
                    if direction in DIRECTION_SHORT_LONG_MAP.keys():
                        direction = DIRECTION_SHORT_LONG_MAP[direction]
                    world.go(direction)
                elif "30" in verb_classes:  # Look
                    if len(command_minus_verb) == 0:
                        world.look()
                        world.step()
                    else:
                        try:
                            direct_object = get_direct_object(command)
                            if world.act_on("look", direct_object):
                                world.step()
                            else:
                                console.warning(
                                    f"Could not find a {direct_object} to look at"
                                )
                        except ParseException as pe:
                            console.warning(pe)
                elif verb == "WAIT":  # Wait
                    world.step()
                elif "37" in verb_classes:  # Tell/Ask
                    # Handle speaking
                    num_quotes = command_minus_verb.count('"')
                    if num_quotes != 2:
                        console.warning(
                            f'When speaking, you must wrap your text in double-quotes.  For example: TELL JUAN "Hello!"'
                        )
                        continue
                    target_name = command_minus_verb[
                        : command_minus_verb.find('"')
                    ].strip()
                    statement = command_minus_verb[command_minus_verb.find('"') - 1 :]

                    target_agent = None
                    missing_target_agent = None
                    for agent_id, agent in world.agents.items():
                        if target_name.lower() in [
                            x.lower() for x in agent.profile.names
                        ]:
                            if (
                                agent_id in world.active_agents
                                and agent.room_id == world.current_room_id
                            ):
                                target_agent = agent
                            missing_target_agent = agent

                    if target_agent is None:
                        if missing_target_agent is not None:
                            console.print(
                                f"{missing_target_agent.profile.name} is not nearby."
                            )
                        else:
                            console.warning(
                                f"Sorry, I don't know who {target_name} is."
                            )
                    else:
                        answer = converse(target_agent, statement)
                        if answer is not None:
                            console.debug("(RAW ANSWER)", answer)
                            console.print(Markdown("> " + answer.strip('"')))
                            console.print("\n")

                            if (
                                len(target_agent.friend_questions) > 0
                                and target_agent.friend_points < 2
                            ):
                                is_more_friendly = check_if_more_friendly(
                                    target_agent,
                                    statement,
                                )

                                if is_more_friendly:
                                    target_agent.friend_points += 1
                                    if target_agent.friend_points >= 2:
                                        console.print(
                                            f"[light_green]{target_agent.profile.name} is now your friend!\n[/]"
                                        )

                                        if target_agent.uid == "port_security_officer":
                                            console.print(
                                                Markdown(
                                                    """```You made your first friend!

When a character becomes your friend, they will give you permission to do things that you couldn't before.```"""
                                                )
                                            )
                                    else:
                                        console.print(
                                            f"[green]{target_agent.profile.name} is more friendly towards you.\n[/]"
                                        )

                                        if (
                                            target_agent.friend_points == 1
                                            and target_agent.uid
                                            == "port_security_officer"
                                        ):
                                            console.print(
                                                Markdown(
                                                    """```Good job!  You found out what Derrick likes to talk about and you mentioned it in your conversation.  This earned you a friend point with Derrick.  Friend points persist across playthroughs: once you make a friend, you have a friend for life.  Keep earning friend points to make Derrick a friend```"""
                                                )
                                            )

                            world.step()
                else:
                    direct_object = get_direct_object(command)
                    if world.act_on(verb, direct_object):
                        world.step()
                    else:
                        console.warning(f"Could not find a {direct_object} to {verb}")
            while world.waiting_for_player is False:
                world.step()


if __name__ == "__main__":
    play()
